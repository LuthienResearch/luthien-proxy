"""Throttled-network performance scenarios (P10b).

Simulates Sami's Tailscale Funnel deployment shape (~1 Mbps + 300 ms RTT)
via Playwright CDP Network.emulateNetworkConditions. PR #1 (perf-baseline)
records measurements only; SLO assertions require PERF_THROTTLE_BASELINE=1.

Chromium-only: Firefox and WebKit do not support CDP bandwidth shaping.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Page

from luthien_proxy.perf.seeding import seed_sami_like

from .conftest import measure_page_load

EVIDENCE_DIR = Path(".sisyphus/evidence")

# CDP throttle parameters — match Sami's Tailscale Funnel free-tier shape.
THROTTLE_DOWNLOAD_BPS: int = 125_000  # bytes/sec (~1 Mbps)
THROTTLE_UPLOAD_BPS: int = 125_000  # bytes/sec (~1 Mbps)
THROTTLE_LATENCY_MS: int = 300  # ms additional latency (RTT)

_SAMI_LIVE_SESSION = "perf-seed-sami-442msg"
N_RUNS: int = 3  # 3 runs; report median


@pytest.fixture(scope="session")
def seeded_sami(perf_db_url: str) -> None:  # noqa: ARG001
    db_path = Path.home() / ".luthien" / "perf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM conversation_calls WHERE session_id LIKE ?",
            ("perf-seed-sami-%",),
        ).fetchone()
        if count == 0:
            seed_sami_like("sqlite")
    finally:
        conn.close()


@pytest.fixture
async def throttled_page(playwright_browser: Browser) -> AsyncIterator[Page]:
    """Fresh browser context with CDP network throttling pre-applied.

    Attaches a CDP session and calls Network.emulateNetworkConditions before
    yielding the page. Each test gets an isolated context with no cookie or
    cache bleed.
    """
    context = await playwright_browser.new_context()
    page = await context.new_page()
    cdp = await context.new_cdp_session(page)
    await cdp.send("Network.enable")
    await cdp.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "downloadThroughput": THROTTLE_DOWNLOAD_BPS,
            "uploadThroughput": THROTTLE_UPLOAD_BPS,
            "latency": THROTTLE_LATENCY_MS,
        },
    )
    yield page
    await context.close()


def _median(values: list[float]) -> float:
    return statistics.median(values)


def _save_throttled_results(route: str, runs_ms: list[float]) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result: dict[str, Any] = {
        "fixture": "sami-like",
        "route": route,
        "timestamp": ts,
        "throttle_config": {
            "download_bps": THROTTLE_DOWNLOAD_BPS,
            "upload_bps": THROTTLE_UPLOAD_BPS,
            "latency_ms": THROTTLE_LATENCY_MS,
        },
        "n_runs": N_RUNS,
        "runs_ms": runs_ms,
        "median_ms": _median(runs_ms),
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EVIDENCE_DIR / f"perf-results-throttled-sami-like-{ts}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_throttle_actually_throttles(
    throttled_page: Page,
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    seeded_sami: None,  # noqa: ARG001
) -> None:
    """Sanity check: throttled TTFB must exceed 100 ms on a localhost request.

    Unthrottled Chromium–localhost TTFB is typically < 20 ms. With 300 ms
    of additional latency configured via CDP, TTFB must be > 100 ms,
    confirming throttling is actually active.
    """
    await throttled_page.set_extra_http_headers(admin_headers)
    url = f"{perf_gateway_url}/history"
    metrics = await measure_page_load(throttled_page, url)

    assert metrics.ttfb_ms >= 100, (
        f"CDP throttling sanity check failed: TTFB={metrics.ttfb_ms:.0f} ms < 100 ms — "
        "throttling may not be active. Check CDP session attachment."
    )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_throttled_history_page(
    throttled_page: Page,
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    seeded_sami: None,  # noqa: ARG001
) -> None:
    """Baseline: /history TTFB under ~1 Mbps + 300 ms RTT (sami-like fixture).

    PR #1 records baseline only. Set PERF_THROTTLE_BASELINE=1 to enable
    the SLO assertion (< 5 000 ms throttled, matching AGENTS.md).
    """
    assert_slo = os.environ.get("PERF_THROTTLE_BASELINE") == "1"
    await throttled_page.set_extra_http_headers(admin_headers)
    url = f"{perf_gateway_url}/history"

    runs: list[float] = []
    for _ in range(N_RUNS):
        m = await measure_page_load(throttled_page, url)
        runs.append(m.ttfb_ms)

    median_ms = _median(runs)
    _save_throttled_results(route="/history", runs_ms=runs)

    if assert_slo:
        assert median_ms < 5_000, f"Throttled /history SLO: median={median_ms:.0f} ms > 5 000 ms"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_throttled_conversation_live(
    throttled_page: Page,
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    seeded_sami: None,  # noqa: ARG001
) -> None:
    """Baseline: /conversation/live/{id} TTFB under ~1 Mbps + 300 ms RTT.

    Uses perf-seed-sami-442msg (the canonical 442-message session) to match
    Sami's largest real session. PR #1 records baseline only.
    """
    assert_slo = os.environ.get("PERF_THROTTLE_BASELINE") == "1"
    await throttled_page.set_extra_http_headers(admin_headers)
    url = f"{perf_gateway_url}/conversation/live/{_SAMI_LIVE_SESSION}"

    runs: list[float] = []
    for _ in range(N_RUNS):
        m = await measure_page_load(throttled_page, url)
        runs.append(m.ttfb_ms)

    median_ms = _median(runs)
    _save_throttled_results(
        route=f"/conversation/live/{_SAMI_LIVE_SESSION}",
        runs_ms=runs,
    )

    if assert_slo:
        assert median_ms < 5_000, f"Throttled live-conversation SLO: median={median_ms:.0f} ms > 5 000 ms"
