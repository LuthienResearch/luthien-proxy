#!/usr/bin/env python3
"""Render a static HTML dashboard from automated maintenance results.

Reads every results.json under <runs_dir>/<run_id>/ and emits:
  <public_dir>/index.html       — overview, last N runs
  <public_dir>/runs/<id>.html    — per-run page with logs

Run with:
  python3 dashboard.py --runs-dir <path> --public-dir <path> --retention 30
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

STATUS_COLOR = {
    "pass": "#1f9d55",
    "fail": "#c53030",
    "error": "#dd6b20",
    "skip": "#718096",
    "unknown": "#a0aec0",
    "opened_pr": "#3182ce",
    "no_diff": "#718096",
    "timeout": "#dd6b20",
    "forbidden_paths": "#c53030",
}

CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 -apple-system, system-ui, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 980px; padding: 0 1rem;
       background: Canvas; color: CanvasText; }
h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.sub { color: #666; margin-bottom: 1.5rem; font-size: 0.9rem; }
table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
th, td { text-align: left; padding: 0.5rem 0.75rem;
         border-bottom: 1px solid #00000020; }
th { font-weight: 600; font-size: 0.85rem; text-transform: uppercase;
     color: #666; letter-spacing: 0.04em; }
.pill { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 9999px;
        color: white; font-size: 0.75rem; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.04em; }
.run-link { font-family: ui-monospace, "SF Mono", monospace; }
a { color: #3182ce; text-decoration: none; }
a:hover { text-decoration: underline; }
pre { background: #00000010; padding: 0.75rem; border-radius: 6px;
      overflow-x: auto; font-size: 0.8rem; line-height: 1.4;
      white-space: pre-wrap; word-break: break-word; }
details { margin: 0.5rem 0; }
summary { cursor: pointer; font-weight: 600; padding: 0.25rem 0; }
.check-grid { display: grid; grid-template-columns: 1fr auto auto;
              gap: 0.5rem 1rem; align-items: center; margin: 1rem 0; }
.check-name { font-weight: 600; }
.check-meta { color: #666; font-size: 0.85rem; font-family: ui-monospace, monospace; }
.muted { color: #666; }
"""


def pill(status: str) -> str:
    color = STATUS_COLOR.get(status, "#a0aec0")
    return f'<span class="pill" style="background:{color}">{html.escape(status)}</span>'


def fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def load_runs(runs_dir: Path) -> list[dict]:
    runs = []
    if not runs_dir.exists():
        return runs
    for child in sorted(runs_dir.iterdir(), reverse=True):
        rj = child / "results.json"
        if not rj.exists():
            continue
        try:
            data = json.loads(rj.read_text())
        except json.JSONDecodeError:
            continue
        data["_dir"] = child
        runs.append(data)
    return runs


def render_index(runs: list[dict]) -> str:
    rows = []
    for r in runs:
        rid = r.get("run_id", "?")
        overall = r.get("overall", "unknown")
        checks = r.get("checks", {})
        check_pills = " ".join(
            f'<span title="{html.escape(n)}">{pill(c.get("status", "unknown"))}</span>' for n, c in checks.items()
        )
        autofix = r.get("autofix") or {}
        af_html = ""
        if autofix:
            af_html = pill(autofix.get("status", "unknown"))
            if autofix.get("pr_url"):
                af_html = f'<a href="{html.escape(autofix["pr_url"])}">{af_html}</a>'
        rows.append(
            f"""<tr>
              <td><a class="run-link" href="runs/{html.escape(rid)}.html">{html.escape(rid)}</a></td>
              <td>{pill(overall)}</td>
              <td>{check_pills}</td>
              <td>{af_html}</td>
              <td class="muted">{fmt_dt(r.get("finished_at") or r.get("started_at"))}</td>
            </tr>"""
        )
    if not rows:
        rows = ['<tr><td colspan="5" class="muted">No runs yet.</td></tr>']

    last = runs[0] if runs else None
    last_html = ""
    if last:
        last_html = (
            f'<p class="sub">Last run <strong>{html.escape(last.get("run_id", "?"))}'
            f"</strong>: {pill(last.get('overall', 'unknown'))} "
            f"on {html.escape(last.get('host', '?'))} "
            f"at {fmt_dt(last.get('finished_at') or last.get('started_at'))}.</p>"
        )

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>luthien-proxy maintenance</title>
<style>{CSS}</style>
</head><body>
<h1>luthien-proxy maintenance</h1>
{last_html}
<table>
<thead><tr><th>Run</th><th>Overall</th><th>Checks</th><th>Autofix</th><th>Finished</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
<p class="sub">Generated {fmt_dt(datetime.now(timezone.utc).isoformat())}.</p>
</body></html>
"""


def render_run(r: dict) -> str:
    rid = r.get("run_id", "?")
    checks_html = []
    for name, c in r.get("checks", {}).items():
        log_path = r["_dir"] / c.get("log", "")
        log_text = ""
        if log_path.exists():
            log_text = log_path.read_text(errors="replace")[-50_000:]
        report_html = ""
        report_rel = c.get("report")
        if report_rel:
            report = r["_dir"] / report_rel
            if report.exists():
                report_html = (
                    f"<details open><summary>Report</summary><pre>{html.escape(report.read_text())}</pre></details>"
                )
        checks_html.append(
            f"""<section>
              <h2>{html.escape(name)} {pill(c.get("status", "unknown"))}</h2>
              <p class="check-meta">duration {c.get("duration_s", "?")}s
                 · exit {c.get("exit_code", "?")}</p>
              {report_html}
              <details><summary>Log ({len(log_text)} chars, tail)</summary>
              <pre>{html.escape(log_text)}</pre></details>
            </section>"""
        )

    autofix = r.get("autofix") or {}
    af_section = ""
    if autofix:
        af_log = r["_dir"] / "autofix_session.log"
        af_log_text = af_log.read_text(errors="replace")[-50_000:] if af_log.exists() else ""
        af_summary = r["_dir"] / "autofix_summary.md"
        af_summary_text = af_summary.read_text() if af_summary.exists() else ""
        pr = autofix.get("pr_url")
        pr_link = f'<p>PR: <a href="{html.escape(pr)}">{html.escape(pr)}</a></p>' if pr else ""
        af_section = f"""<section>
          <h2>autofix {pill(autofix.get("status", "unknown"))}</h2>
          <p class="check-meta">duration {autofix.get("duration_s", "?")}s
             · exit {autofix.get("exit_code", "?")}</p>
          {pr_link}
          {f"<details open><summary>Summary</summary><pre>{html.escape(af_summary_text)}</pre></details>" if af_summary_text else ""}
          <details><summary>Session log ({len(af_log_text)} chars, tail)</summary>
          <pre>{html.escape(af_log_text)}</pre></details>
        </section>"""

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>maintenance {html.escape(rid)}</title>
<style>{CSS}</style>
</head><body>
<p><a href="../index.html">← all runs</a></p>
<h1>maintenance {html.escape(rid)} {pill(r.get("overall", "unknown"))}</h1>
<p class="sub">host {html.escape(r.get("host", "?"))} · started {fmt_dt(r.get("started_at"))}
 · finished {fmt_dt(r.get("finished_at"))}</p>
{"".join(checks_html)}
{af_section}
</body></html>
"""


def prune(runs_dir: Path, retention: int) -> None:
    if retention <= 0:
        return
    children = sorted([c for c in runs_dir.iterdir() if c.is_dir()], reverse=True)
    for old in children[retention:]:
        shutil.rmtree(old, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--public-dir", required=True)
    ap.add_argument("--retention", type=int, default=30)
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir)
    public_dir = Path(args.public_dir)
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "runs").mkdir(exist_ok=True)

    prune(runs_dir, args.retention)
    runs = load_runs(runs_dir)[: args.retention]

    (public_dir / "index.html").write_text(render_index(runs))
    for r in runs:
        (public_dir / "runs" / f"{r.get('run_id', 'unknown')}.html").write_text(render_run(r))


if __name__ == "__main__":
    main()
