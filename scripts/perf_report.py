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


def _page_timing_records(results: list[dict]) -> list[dict]:
    return [r for r in results if "scenarios" in r]


def _throttled_records(results: list[dict]) -> list[dict]:
    return [r for r in results if "route" in r and "runs_ms" in r]


def _sse_memory_records(results: list[dict]) -> list[dict]:
    return [r for r in results if "heap_growth_pct" in r]


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
    records = _page_timing_records(results)
    if not records:
        return "\n".join([header, "", "_NO DATA YET — run `scripts/run_perf.sh` to populate._", ""])

    data: dict[str, dict[str, dict]] = {}
    fixtures: set[str] = set()
    for rec in records:
        fixture = rec.get("fixture", "unknown")
        fixtures.add(fixture)
        for scenario in rec.get("scenarios", []):
            page = scenario.get("page", "?")
            data.setdefault(page, {})[fixture] = {
                "cold_ms": scenario.get("cold_ms"),
                "median_ms": scenario.get("median_ms"),
                "p95_ms": scenario.get("p95_ms"),
                "ttfb_ms": scenario.get("ttfb_ms"),
                "transfer_bytes": scenario.get("transfer_bytes"),
            }

    pages = sorted(data.keys())
    fixture_list = sorted(fixtures)
    col_header = " | ".join(f"{f} cold_ms | {f} median_ms | {f} p95_ms" for f in fixture_list)
    col_sep = " | ".join("--- | --- | ---" for _ in fixture_list)
    lines = [header, "", f"| Page | {col_header} |", f"|------| {col_sep} |"]

    for page in pages:
        cells: list[str] = []
        for fixture in fixture_list:
            fdata = data[page].get(fixture, {})
            cells.append(str(round(fdata["cold_ms"], 0)) if fdata.get("cold_ms") is not None else "—")
            cells.append(str(round(fdata["median_ms"], 0)) if fdata.get("median_ms") is not None else "—")
            cells.append(str(round(fdata["p95_ms"], 0)) if fdata.get("p95_ms") is not None else "—")
        lines.append(f"| {page} | " + " | ".join(cells) + " |")

    lines.append("")
    return "\n".join(lines)


def _section_throttled(results: list[dict]) -> str:
    header = "## Throttled (sami-like)"
    records = _throttled_records(results)
    if not records:
        return "\n".join([header, "", "_NO DATA YET_", ""])

    lines = [
        header,
        "",
        "| Route | Fixture | N runs | Median ms | Config |",
        "|-------|---------|--------|-----------|--------|",
    ]
    for rec in sorted(records, key=lambda r: r.get("route", "")):
        route = rec.get("route", "?")
        fixture = rec.get("fixture", "?")
        n_runs = rec.get("n_runs", "?")
        median = rec.get("median_ms")
        cfg = rec.get("throttle_config", {})
        cfg_str = f"{cfg.get('download_bps', '?')} bps / {cfg.get('latency_ms', '?')} ms RTT"
        lines.append(
            f"| {route} | {fixture} | {n_runs} | {round(median, 0) if median is not None else '—'} | {cfg_str} |"
        )
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
    records = _sse_memory_records(results)
    if not records:
        return "\n".join([header, "", "_NO DATA YET_", ""])

    rec = records[-1]
    heap_first_mb = round(rec.get("heap_first_bytes", 0) / (1024 * 1024), 1)
    heap_last_mb = round(rec.get("heap_last_bytes", 0) / (1024 * 1024), 1)
    growth_pct = round(rec.get("heap_growth_pct", 0), 1)
    hold_s = rec.get("hold_seconds", "?")
    lines = [
        header,
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| heap_first_mb | {heap_first_mb} |",
        f"| heap_last_mb | {heap_last_mb} |",
        f"| heap_growth_pct | {growth_pct}% |",
        f"| hold_seconds | {hold_s} |",
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
    has_data = bool(_page_timing_records(results) or _throttled_records(results) or _sse_memory_records(results))

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

    for rec in _page_timing_records(results):
        fixture = rec.get("fixture", "?")
        for scenario in rec.get("scenarios", []):
            page = scenario.get("page", "?")
            p95 = scenario.get("p95_ms", 0)
            if isinstance(p95, (int, float)) and p95 > 1000:
                hotspots.append(f"`{page}` ({fixture}) p95={p95:.0f}ms — exceeds 1s SLO")

    for rec in _sse_memory_records(results):
        growth_pct = rec.get("heap_growth_pct", 0)
        if isinstance(growth_pct, (int, float)) and growth_pct > 50:
            hotspots.append(f"SSE heap growth={growth_pct:.1f}% over session — possible unbounded accumulation")

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
