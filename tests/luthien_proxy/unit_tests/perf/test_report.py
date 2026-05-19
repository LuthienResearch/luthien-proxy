from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "scripts"))

import perf_report  # noqa: E402

_MOCK_RESULTS = [
    {
        "type": "page_timings",
        "data": {
            "history_list": {"sami": {"median_ms": 450, "p95_ms": 780}},
            "session_detail": {"sami": {"median_ms": 310, "p95_ms": 550}},
        },
    },
    {
        "type": "throttled",
        "data": {
            "history_list": {"throttled_sami": {"median_ms": 2100, "p95_ms": 3800}},
        },
    },
    {
        "type": "transcript_open",
        "data": {"first_turn_painted_ms": 650},
    },
    {
        "type": "sse_memory",
        "data": {"heap_growth_mb": 12.5, "events_count": 442},
    },
    {
        "type": "server_timing",
        "data": {"db_ms": 45.2, "serialize_ms": 12.1, "render_ms": 8.3},
    },
    {
        "type": "payload_size",
        "data": {
            "/api/history/sessions": {"bytes": 4096},
            "/api/history/sessions/{id}": {"bytes": 78432},
        },
    },
]

_MOCK_QUERY_PLANS = "## Query: session_list\n\nSEARCH ce USING INDEX ...\n"

_REQUIRED_SECTIONS = [
    "## Hardware & Versions",
    "## Per-Page Timings",
    "## Throttled (sami-like)",
    "## Transcript Open",
    "## SSE Memory Growth",
    "## Server-Timing Breakdown",
    "## Payload Size Breakdown",
    "## Query Plans",
    "## Top Hotspots",
]

_COMMON_KWARGS = dict(
    results=_MOCK_RESULTS,
    query_plans=_MOCK_QUERY_PLANS,
    git_sha="abc123def456",
    playwright_ver="1.50.0",
    generated_at="2000-01-01T00:00:00+00:00",
    ram="16 GB",
)


def test_report_has_required_sections():
    report = perf_report.generate_report(**_COMMON_KWARGS)
    for section in _REQUIRED_SECTIONS:
        assert section in report, f"Missing section: {section!r}"


def test_report_has_metadata():
    report = perf_report.generate_report(**_COMMON_KWARGS)
    assert "git_sha: abc123def456" in report
    assert "browser_version: 1.50.0" in report
    assert "backend: sqlite" in report


def test_report_deterministic():
    report1 = perf_report.generate_report(**_COMMON_KWARGS)
    report2 = perf_report.generate_report(**_COMMON_KWARGS)
    assert report1 == report2


def test_report_no_data_placeholder():
    report = perf_report.generate_report(
        results=[],
        query_plans="_No query plans._",
        git_sha="abc123",
        playwright_ver="1.50.0",
        generated_at="2000-01-01T00:00:00+00:00",
        ram="16 GB",
    )
    for section in _REQUIRED_SECTIONS:
        assert section in report, f"Missing section with no data: {section!r}"
    assert "NO DATA YET" in report
