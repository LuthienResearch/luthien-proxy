#!/usr/bin/env python3
"""Generate a Markdown performance baseline report from perf test results.

Reads .sisyphus/evidence/perf-results-*.json files and embeds
.sisyphus/evidence/baseline-query-plans.md.  When no JSON files exist
(P10-P12 not yet run), every data section is rendered with a NO DATA YET
placeholder so the file is still structurally valid for diffing.

Usage:
    uv run python scripts/perf_report.py --output .sisyphus/evidence/perf-report-baseline.md
    uv run python scripts/perf_report.py --output out.md --deterministic-mode
"""

from __future__ import annotations

import argparse
import glob
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_EVIDENCE_DIR = _REPO_ROOT / ".sisyphus" / "evidence"


def _git_sha(repo_root: Path | None = None) -> str:
    root = repo_root or _REPO_ROOT
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _playwright_version() -> str:
    try:
        return importlib.metadata.version("playwright")
    except Exception:
        return "unknown"


def _ram_info() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            gb = int(result.stdout.strip()) // (1024**3)
            return f"{gb} GB"
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return f"{kb // (1024**2)} GB"
    except Exception:
        pass
    return "unknown"


def load_results(evidence_dir: Path | None = None) -> list[dict]:
    d = evidence_dir or _EVIDENCE_DIR
    results: list[dict] = []
    for path in sorted(glob.glob(str(d / "perf-results-*.json"))):
        try:
            with open(path) as f:
                results.append(json.load(f))
        except Exception:
            continue
    return results


def load_query_plans(evidence_dir: Path | None = None) -> str:
    d = evidence_dir or _EVIDENCE_DIR
    path = d / "baseline-query-plans.md"
    if path.exists():
        return path.read_text()
    return "_Query plans not yet captured. Run `scripts/perf_explain.py` first._\n"


def _find_result(results: list[dict], type_: str) -> dict | None:
    for r in results:
        if r.get("type") == type_:
            return r
    return None


def _section_hardware(git_sha: str, playwright_ver: str, ram: str, backend: str = "sqlite") -> str:
    rows = [
        ("Machine", platform.machine()),
        ("Processor", platform.processor() or platform.machine()),
        ("RAM", ram),
        ("OS", f"{platform.system()} {platform.release()}"),
        ("Python", platform.python_version()),
        ("git_sha", f"`{git_sha}`"),
        ("DB backend", backend),
        ("Playwright", playwright_ver),
    ]
    table = ["| Field | Value |", "|-------|-------|"]
    table.extend(f"| {k} | {v} |" for k, v in rows)
    return "\n".join(["## Hardware & Versions", ""] + table + [""])


def _section_per_page_timings(results: list[dict]) -> str:
    header = "## Per-Page Timings"
    r = _find_result(results, "page_timings")
    if not r:
        return "\n".join([header, "", "_NO DATA YET — run `scripts/run_perf.sh` to populate._", ""])

    data = r.get("data", {})
    pages = sorted(data.keys())
    fixtures: set[str] = set()
    for page_data in data.values():
        fixtures.update(page_data.keys())
    fixture_list = sorted(fixtures)

    col_header = " | ".join(f"{f} median_ms | {f} p95_ms" for f in fixture_list)
    col_sep = " | ".join("--- | ---" for _ in fixture_list)
    lines = [header, "", f"| Page | {col_header} |", f"|------| {col_sep} |"]

    for page in pages:
        cells: list[str] = []
        for fixture in fixture_list:
            fdata = data[page].get(fixture, {})
            cells.append(str(fdata.get("median_ms", "—")))
            cells.append(str(fdata.get("p95_ms", "—")))
        lines.append(f"| {page} | " + " | ".join(cells) + " |")

    lines.append("")
    return "\n".join(lines)


def _section_throttled(results: list[dict]) -> str:
    header = "## Throttled (sami-like)"
    r = _find_result(results, "throttled")
    if not r:
        return "\n".join([header, "", "_NO DATA YET_", ""])

    data = r.get("data", {})
    lines = [header, "", "| Page | Fixture | Median ms | P95 ms |", "|------|---------|-----------|--------|"]
    for page in sorted(data.keys()):
        for fixture, fdata in sorted(data[page].items()):
            median = fdata.get("median_ms", "—")
            p95 = fdata.get("p95_ms", "—")
            lines.append(f"| {page} | {fixture} | {median} | {p95} |")
    lines.append("")
    return "\n".join(lines)


def _section_transcript_open(results: list[dict]) -> str:
    header = "## Transcript Open"
    r = _find_result(results, "transcript_open")
    if not r:
        return "\n".join([header, "", "_NO DATA YET_", ""])

    data = r.get("data", {})
    lines = [
        header,
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| first_turn_painted_ms | {data.get('first_turn_painted_ms', '—')} |",
        "",
    ]
    return "\n".join(lines)


def _section_sse_memory(results: list[dict]) -> str:
    header = "## SSE Memory Growth"
    r = _find_result(results, "sse_memory")
    if not r:
        return "\n".join([header, "", "_NO DATA YET_", ""])

    data = r.get("data", {})
    lines = [
        header,
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| heap_growth_mb | {data.get('heap_growth_mb', '—')} |",
        f"| events_count | {data.get('events_count', '—')} |",
        "",
    ]
    return "\n".join(lines)


def _section_server_timing(results: list[dict]) -> str:
    header = "## Server-Timing Breakdown"
    r = _find_result(results, "server_timing")
    if not r:
        return "\n".join([header, "", "_NO DATA YET_", ""])

    data = r.get("data", {})
    lines = [header, "", "| Phase | Median ms |", "|-------|-----------|"]
    for phase in ("db", "serialize", "render"):
        lines.append(f"| {phase} | {data.get(f'{phase}_ms', '—')} |")
    lines.append("")
    return "\n".join(lines)


def _section_payload_size(results: list[dict]) -> str:
    header = "## Payload Size Breakdown"
    r = _find_result(results, "payload_size")
    if not r:
        return "\n".join([header, "", "_NO DATA YET_", ""])

    data = r.get("data", {})
    lines = [header, "", "| Endpoint | Bytes |", "|----------|-------|"]
    for endpoint in sorted(data.keys()):
        lines.append(f"| {endpoint} | {data[endpoint].get('bytes', '—')} |")
    lines.append("")
    return "\n".join(lines)


def _section_query_plans(query_plans: str) -> str:
    return "\n".join(["## Query Plans", "", query_plans.strip(), ""])


def _section_top_hotspots(results: list[dict]) -> str:
    header = "## Top Hotspots"
    has_data = any(
        r.get("type") in ("page_timings", "throttled", "server_timing", "payload_size", "sse_memory") for r in results
    )

    if not has_data:
        lines = [
            header,
            "",
            "_NO DATA YET — hotspots will be derived from measurement results._",
            "",
            "**Known candidates (from code review):**",
            "",
            "1. `history_list.html:514` — hardcodes `?limit=10000` (sends full dataset on every load)",
            "2. `conversation_live.js:92-118` — `loadInitial()` fetches entire session upfront",
            "3. `conversation_live.js:215-244` — full DOM re-render on every SSE event",
            "4. `conversation_live.js:164-172` — unbounded `rawEvents[callId]` array (memory leak risk)",
            "5. `history_list.html:423-448` — client-side filter runs on every keystroke",
            "",
            "**Query plan risks:**",
            "",
            "- `session_list`: 2× TEMP B-TREE (COUNT DISTINCT + ORDER BY) — scales poorly with row count",
            "- `recent_calls`: SCAN on all rows — O(n) over conversation_events",
            "",
        ]
        return "\n".join(lines)

    hotspots: list[str] = []

    r_page = _find_result(results, "page_timings")
    if r_page:
        for page, fixtures in r_page.get("data", {}).items():
            for fixture, stats in fixtures.items():
                p95 = stats.get("p95_ms", 0)
                if isinstance(p95, (int, float)) and p95 > 1000:
                    hotspots.append(f"`{page}` ({fixture}) p95={p95}ms — exceeds 1s SLO")

    r_payload = _find_result(results, "payload_size")
    if r_payload:
        for endpoint, stats in r_payload.get("data", {}).items():
            bytes_ = stats.get("bytes", 0)
            if isinstance(bytes_, int) and bytes_ > 50_000:
                hotspots.append(f"`{endpoint}` payload={bytes_ // 1024}KB — exceeds 50KB budget")

    r_sse = _find_result(results, "sse_memory")
    if r_sse:
        growth = r_sse.get("data", {}).get("heap_growth_mb", 0)
        if isinstance(growth, (int, float)) and growth > 10:
            hotspots.append(f"SSE heap growth={growth}MB over session — unbounded accumulation risk")

    lines = [header, ""]
    if hotspots:
        lines.extend(f"- {h}" for h in hotspots)
    else:
        lines.append("_No hotspots detected above threshold. See individual sections for details._")
    lines.append("")
    return "\n".join(lines)


def generate_report(
    results: list[dict],
    query_plans: str,
    git_sha: str,
    playwright_ver: str,
    generated_at: str | None = None,
    ram: str | None = None,
    backend: str = "sqlite",
) -> str:
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    ram_str = ram or _ram_info()

    parts = [
        f"git_sha: {git_sha}",
        f"browser_version: {playwright_ver}",
        f"backend: {backend}",
        f"generated_at: {timestamp}",
        "",
        "# Luthien Admin UI — Performance Baseline Report",
        "",
        _section_hardware(git_sha, playwright_ver, ram_str, backend=backend),
        _section_per_page_timings(results),
        _section_throttled(results),
        _section_transcript_open(results),
        _section_sse_memory(results),
        _section_server_timing(results),
        _section_payload_size(results),
        _section_query_plans(query_plans),
        _section_top_hotspots(results),
    ]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate perf baseline Markdown report")
    parser.add_argument("--output", required=True, help="Path to write the Markdown report")
    parser.add_argument(
        "--deterministic-mode",
        action="store_true",
        help="Fix timestamp to epoch so output is byte-identical across runs (for reproducibility testing)",
    )
    parser.add_argument(
        "--backend",
        default="sqlite",
        help="DB backend label to embed in the report (default: sqlite)",
    )
    args = parser.parse_args()

    results = load_results()
    query_plans = load_query_plans()
    sha = _git_sha()
    pw_ver = _playwright_version()

    generated_at = "2000-01-01T00:00:00+00:00" if args.deterministic_mode else None

    report = generate_report(results, query_plans, sha, pw_ver, generated_at=generated_at, backend=args.backend)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)

    print(f"Report written to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
