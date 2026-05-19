"""Transcript-open performance scenarios (P11).

Measures time-to-first-turn-painted for /conversation/live/{session_id}.

"First-turn-painted" = first child node insertion into #conversation-container
by conversation_live.js after the initial fetch-and-render cycle. A
MutationObserver installed via add_init_script records performance.now() at
that moment, before any page JS runs.

PR #1 (perf-baseline) records measurements only; no SLO is asserted.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Page

from luthien_proxy.perf.seeding import seed_sami_like, seed_sessions

EVIDENCE_DIR = Path(".sisyphus/evidence")

N_RUNS: int = 5

# (fixture_label, session_id) — sami-like primary; tier-100 and tier-1000 secondary.
_FIXTURES: list[tuple[str, str]] = [
    ("sami-like", "perf-seed-sami-442msg"),
    ("tier-100", "perf-seed-100-0001"),
    ("tier-1000", "perf-seed-1000-0001"),
]

# Installed via add_init_script — runs before any page JS on every navigation.
# Guard flag prevents double-observation when called N times on the same page.
# Targets #conversation-container (not #main) to capture the first rendered turn.
_FIRST_TURN_OBSERVER_SCRIPT = """
if (!window.__transcriptPerfInstalled) {
    window.__transcriptPerfInstalled = true;
    window.__firstTurnPainted = null;

    function _setupTranscriptObserver() {
        var container = document.getElementById('conversation-container');
        if (!container) { return; }
        var obs = new MutationObserver(function(mutations) {
            if (window.__firstTurnPainted !== null) { return; }
            for (var i = 0; i < mutations.length; i++) {
                if (mutations[i].addedNodes.length > 0) {
                    window.__firstTurnPainted = performance.now();
                    obs.disconnect();
                    break;
                }
            }
        });
        obs.observe(container, { childList: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _setupTranscriptObserver);
    } else {
        _setupTranscriptObserver();
    }
}
"""


@pytest.fixture(scope="session")
def seeded_transcript_fixtures(perf_db_url: str) -> None:  # noqa: ARG001
    db_path = Path.home() / ".luthien" / "perf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        for prefix, seed_fn in [
            ("perf-seed-sami-%", lambda: seed_sami_like("sqlite")),
            ("perf-seed-100-%", lambda: seed_sessions("sqlite", tier=100)),
            ("perf-seed-1000-%", lambda: seed_sessions("sqlite", tier=1000)),
        ]:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM conversation_calls WHERE session_id LIKE ?",
                (prefix,),
            ).fetchone()
            if count == 0:
                seed_fn()
    finally:
        conn.close()


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = min(int(len(sorted_vals) * 0.95), len(sorted_vals) - 1)
    return sorted_vals[idx]


async def _measure_first_turn_painted(page: Page, url: str) -> dict[str, float]:
    """Navigate to url and return first-turn-painted + ancillary metrics.

    Installs _FIRST_TURN_OBSERVER_SCRIPT via add_init_script so the observer
    fires before any page JS. Each navigation resets window state, giving fresh
    timing per run despite the script accumulating across calls.
    """
    await page.add_init_script(_FIRST_TURN_OBSERVER_SCRIPT)
    await page.goto(url, wait_until="networkidle")

    return await page.evaluate("""() => {
        var entries = window.performance.getEntriesByType('navigation');
        var ttfb = 0, load = 0;
        if (entries.length > 0) {
            var nav = entries[0];
            ttfb = nav.responseStart;
            load = nav.loadEventEnd;
        } else {
            var t = window.performance.timing;
            var origin = t.fetchStart;
            ttfb = t.responseStart - origin;
            load = t.loadEventEnd - origin;
        }
        return {
            ttfb_ms: ttfb,
            load_ms: load,
            first_turn_painted_ms: window.__firstTurnPainted || 0
        };
    }""")


def _save_transcript_results(
    fixture_label: str,
    session_id: str,
    all_runs: list[dict[str, float]],
    transfer_bytes: int,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ftp_values = [r["first_turn_painted_ms"] for r in all_runs]
    ttfb_values = [r["ttfb_ms"] for r in all_runs]

    result: dict[str, Any] = {
        "fixture": fixture_label,
        "session_id": session_id,
        "timestamp": ts,
        "n_runs": N_RUNS,
        "first_turn_painted": {
            "median_ms": statistics.median(ftp_values),
            "p95_ms": _p95(ftp_values),
            "runs_ms": ftp_values,
        },
        "ttfb": {
            "median_ms": statistics.median(ttfb_values),
            "runs_ms": ttfb_values,
        },
        "total_render_time_ms": statistics.median([r["load_ms"] for r in all_runs]),
        "response_body_bytes": transfer_bytes,
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EVIDENCE_DIR / f"perf-results-transcript-{fixture_label}-{ts}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)


@pytest.mark.perf
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_label,session_id", _FIXTURES)
async def test_transcript_open(
    fixture_label: str,
    session_id: str,
    playwright_page: Page,
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    seeded_transcript_fixtures: None,  # noqa: ARG001
) -> None:
    """Baseline: time-to-first-turn-painted per fixture tier.

    Parametrized over sami-like (442 msg), tier-100, tier-1000. Five runs per
    fixture; results include median and p95. PR #1 records baseline only.
    """
    await playwright_page.set_extra_http_headers(admin_headers)
    url = f"{perf_gateway_url}/conversation/live/{session_id}"

    all_runs: list[dict[str, float]] = []
    for _ in range(N_RUNS):
        metrics = await _measure_first_turn_painted(playwright_page, url)
        all_runs.append(metrics)

    async with httpx.AsyncClient(headers=admin_headers, follow_redirects=True) as client:
        http_resp = await client.get(url)
    transfer_bytes = len(http_resp.content)

    _save_transcript_results(
        fixture_label=fixture_label,
        session_id=session_id,
        all_runs=all_runs,
        transfer_bytes=transfer_bytes,
    )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_first_turn_painted_500_turns(
    playwright_page: Page,
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    seeded_transcript_fixtures: None,  # noqa: ARG001
) -> None:
    """Baseline: first-turn-painted for the canonical 442-message session.

    perf-seed-sami-442msg is the closest available session to the "500-turn"
    SLO reference in AGENTS.md. PR #1 records baseline only; no SLO asserted.
    """
    session_id = "perf-seed-sami-442msg"
    fixture_label = "sami-442msg"
    await playwright_page.set_extra_http_headers(admin_headers)
    url = f"{perf_gateway_url}/conversation/live/{session_id}"

    all_runs: list[dict[str, float]] = []
    for _ in range(N_RUNS):
        metrics = await _measure_first_turn_painted(playwright_page, url)
        all_runs.append(metrics)

    async with httpx.AsyncClient(headers=admin_headers, follow_redirects=True) as client:
        http_resp = await client.get(url)
    transfer_bytes = len(http_resp.content)

    _save_transcript_results(
        fixture_label=fixture_label,
        session_id=session_id,
        all_runs=all_runs,
        transfer_bytes=transfer_bytes,
    )
