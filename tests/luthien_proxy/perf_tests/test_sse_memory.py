"""SSE memory growth scenarios (P12).

Opens /conversation/live/{session_id} and holds the SSE connection for 60 s,
sampling JSHeapUsedSize every 5 seconds via performance.memory.

NOTE: performance.memory is Chrome-specific and non-standard. Values are
approximate unless Chromium is launched with --enable-precise-memory-info.

The suspected leak: rawEvents[callId] in conversation_live.js:164-172 is an
unbounded dict that accumulates all SSE events per call ID without eviction.

PR #1 (perf-baseline) records baseline only. Set PERF_ASSERT_MEMORY=1 to
enable the heap-growth assertion.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page

from luthien_proxy.perf.seeding import seed_sami_like

_REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = _REPO_ROOT / ".sisyphus" / "evidence"

_HOLD_SECONDS: int = 60
_SAMPLE_INTERVAL_S: int = 5
_SAMI_LIVE_SESSION = "perf-seed-sami-442msg"


@pytest.fixture(scope="session")
def seeded_sami_sse(perf_db_url: str) -> None:  # noqa: ARG001
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


def _save_sse_memory_results(
    session_id: str,
    heap_samples: list[int],
    heap_growth_pct: float,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result: dict[str, Any] = {
        "session_id": session_id,
        "timestamp": ts,
        "hold_seconds": _HOLD_SECONDS,
        "sample_interval_s": _SAMPLE_INTERVAL_S,
        "heap_samples_bytes": heap_samples,
        "heap_first_bytes": heap_samples[0] if heap_samples else 0,
        "heap_last_bytes": heap_samples[-1] if heap_samples else 0,
        "heap_growth_pct": heap_growth_pct,
        "note": ("performance.memory is Chrome-specific. For precise values use --enable-precise-memory-info."),
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EVIDENCE_DIR / f"perf-results-sse-memory-{ts}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)


@pytest.mark.perf
@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_sse_heap_growth_60s(
    playwright_page: Page,
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    seeded_sami_sse: None,  # noqa: ARG001
) -> None:
    """Baseline: JS heap growth over 60 s on the live conversation page.

    Opens perf-seed-sami-442msg, holds the SSE connection, samples
    JSHeapUsedSize every 5 s. Heap growth = (last - first) / first * 100.

    The 90 s timeout (pytest.mark.timeout) covers 60 s hold + navigation
    and evaluation overhead. PR #1 records baseline only; set
    PERF_ASSERT_MEMORY=1 to assert heap growth < 50% over 60 s.
    """
    assert_memory = os.environ.get("PERF_ASSERT_MEMORY") == "1"

    await playwright_page.set_extra_http_headers(admin_headers)
    url = f"{perf_gateway_url}/conversation/live/{_SAMI_LIVE_SESSION}"
    await playwright_page.goto(url, wait_until="networkidle")

    heap_samples: list[int] = []
    n_samples = _HOLD_SECONDS // _SAMPLE_INTERVAL_S

    for _ in range(n_samples):
        await asyncio.sleep(_SAMPLE_INTERVAL_S)
        heap: int = await playwright_page.evaluate(
            "() => window.performance.memory ? window.performance.memory.usedJSHeapSize : 0"
        )
        heap_samples.append(heap)

    if heap_samples and heap_samples[0] > 0:
        heap_growth_pct = (heap_samples[-1] - heap_samples[0]) / heap_samples[0] * 100
    else:
        heap_growth_pct = 0.0

    _save_sse_memory_results(
        session_id=_SAMI_LIVE_SESSION,
        heap_samples=heap_samples,
        heap_growth_pct=heap_growth_pct,
    )

    if assert_memory:
        assert heap_growth_pct < 50.0, (
            f"SSE memory growth: {heap_growth_pct:.1f}% > 50% over {_HOLD_SECONDS}s. "
            "Possible leak in rawEvents[callId] (conversation_live.js:164-172)."
        )
