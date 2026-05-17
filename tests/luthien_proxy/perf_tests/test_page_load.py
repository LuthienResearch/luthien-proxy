"""Per-page performance scenarios — auto-discovers all admin UI routes.

Parametrized by fixture_name x route_path (4 x 11 = 44 test cases).
SLO enforced only on /history and /conversation/live/{id} for sami-like
and tier-1000 fixtures.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute
from playwright.async_api import Page

from luthien_proxy.main import create_app
from luthien_proxy.perf.db import get_perf_db_url
from luthien_proxy.perf.seeding import seed_sami_like, seed_sessions
from luthien_proxy.utils.db import DatabasePool

from .conftest import measure_page_load, n_runs

_REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = _REPO_ROOT / ".sisyphus" / "evidence"
TRACES_DIR = EVIDENCE_DIR / "traces"

_TTFB_SLO_MS: float = 2_000.0
_SLO_FIXTURES: frozenset[str] = frozenset({"sami-like", "tier-1000"})
_SLO_PAGES: frozenset[str] = frozenset({"/history", "/conversation/live/{conversation_id}"})

FIXTURE_NAMES: list[str] = ["sami-like", "tier-100", "tier-1000", "tier-10000"]
N_RUNS: int = 5


def _discover_html_routes() -> list[str]:
    app = create_app(
        api_key="x",
        admin_key="x",
        db_pool=DatabasePool(get_perf_db_url("sqlite")),  # lazy — no connection opened
        redis_client=None,
        startup_policy_path=None,
    )

    excluded_prefixes = ("/api/", "/v1/", "/static/", "/auth/")
    excluded_paths: frozenset[str] = frozenset({"/health", "/ready", "/login"})

    routes: list[str] = []
    for raw_route in app.routes:
        if not isinstance(raw_route, APIRoute):
            continue
        if "GET" not in (raw_route.methods or set()):
            continue
        if raw_route.response_model is not None:
            continue
        path = raw_route.path
        if any(path.startswith(p) for p in excluded_prefixes):
            continue
        if path in excluded_paths:
            continue
        if path.endswith("/{path:path}"):
            continue
        if "{" in path and path != "/conversation/live/{conversation_id}":
            continue
        routes.append(path)

    return sorted(routes)


_ADMIN_ROUTES: list[str] = _discover_html_routes()


def _live_conversation_id(fixture_name: str) -> str:
    if fixture_name == "sami-like":
        return "perf-seed-sami-442msg"
    tier = fixture_name.split("-")[1]
    return f"perf-seed-{tier}-0001"


def _resolve_url(base_url: str, route_path: str, fixture_name: str) -> str:
    if "{conversation_id}" in route_path:
        route_path = route_path.replace("{conversation_id}", _live_conversation_id(fixture_name))
    return base_url.rstrip("/") + route_path


def _slo_enforced(fixture_name: str, route_path: str) -> bool:
    return fixture_name in _SLO_FIXTURES and route_path in _SLO_PAGES


@pytest.fixture(scope="session")
def seeded_perf_db_all(perf_db_url: str) -> None:  # noqa: ARG001
    db_path = Path.home() / ".luthien" / "perf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        for prefix, seed_fn in [
            ("perf-seed-sami-%", lambda: seed_sami_like("sqlite")),
            ("perf-seed-100-%", lambda: seed_sessions("sqlite", tier=100)),
            ("perf-seed-1000-%", lambda: seed_sessions("sqlite", tier=1000)),
            ("perf-seed-10000-%", lambda: seed_sessions("sqlite", tier=10000)),
        ]:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM conversation_calls WHERE session_id LIKE ?",
                (prefix,),
            ).fetchone()
            if count == 0:
                seed_fn()
    finally:
        conn.close()


@pytest.fixture(scope="session")
def perf_results_store() -> Iterator[dict[str, list[dict[str, Any]]]]:
    store: dict[str, list[dict[str, Any]]] = defaultdict(list)
    yield store  # type: ignore[misc]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    for fixture_name, scenarios in store.items():
        if not scenarios:
            continue
        result: dict[str, Any] = {
            "fixture": fixture_name,
            "timestamp": ts,
            "scenarios": scenarios,
        }
        out_path = EVIDENCE_DIR / f"perf-results-{fixture_name}-{ts}.json"
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)


@pytest.mark.perf
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_name,route_path",
    [(f, p) for f in FIXTURE_NAMES for p in _ADMIN_ROUTES],
)
async def test_page_load(
    fixture_name: str,
    route_path: str,
    perf_gateway_url: str,
    playwright_page: Page,
    admin_headers: dict[str, str],
    seeded_perf_db_all: None,  # noqa: ARG001
    perf_results_store: dict[str, list[dict[str, Any]]],
) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    TRACES_DIR.mkdir(parents=True, exist_ok=True)

    url = _resolve_url(perf_gateway_url, route_path, fixture_name)
    slo_ok = _slo_enforced(fixture_name, route_path)

    await playwright_page.set_extra_http_headers(admin_headers)

    safe_page = route_path.replace("/", "_").replace("{", "").replace("}", "")
    trace_path = str(TRACES_DIR / f"trace-{fixture_name}{safe_page}.zip")
    await playwright_page.context.tracing.start(screenshots=True, snapshots=True, sources=True)

    try:

        async def _load_once() -> float:
            m = await measure_page_load(playwright_page, url)
            return m.ttfb_ms

        run_stats = await n_runs(_load_once, n=N_RUNS)
        final_m = await measure_page_load(playwright_page, url)

        async with httpx.AsyncClient(headers=admin_headers, follow_redirects=True) as client:
            http_resp = await client.get(url)
        transfer_bytes: int = len(http_resp.content)
        transfer_encoding: str = http_resp.headers.get("transfer-encoding", "identity")

    finally:
        await playwright_page.context.tracing.stop(path=trace_path)

    scenario: dict[str, Any] = {
        "page": route_path,
        "url": url,
        "slo_enforced": slo_ok,
        "cold_ms": run_stats.cold_ms,
        "median_ms": run_stats.warm_median_ms,
        "p95_ms": run_stats.warm_p95_ms,
        "ttfb_ms": final_m.ttfb_ms,
        "dcl_ms": final_m.dcl_ms,
        "ttfm_ms": final_m.ttfm_ms,
        "transfer_bytes": transfer_bytes,
        "transfer_encoding": transfer_encoding,
    }
    perf_results_store[fixture_name].append(scenario)

    if slo_ok:
        assert run_stats.warm_median_ms < _TTFB_SLO_MS, (
            f"TTFB SLO failed [{fixture_name}][{route_path}]: "
            f"warm_median={run_stats.warm_median_ms:.0f} ms "
            f"> threshold={_TTFB_SLO_MS:.0f} ms"
        )
