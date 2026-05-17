#!/usr/bin/env python3
"""Capture EXPLAIN QUERY PLAN for the top slow queries against the perf DB.

Usage:
    uv run python scripts/perf_explain.py --backend sqlite
    uv run python scripts/perf_explain.py --backend postgres

Outputs: .sisyphus/evidence/baseline-query-plans.md

Safety: refuses to connect if DATABASE_URL points to the dev DB (local.db).
"""

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from luthien_proxy.perf.db import ensure_perf_isolation, get_perf_db_url, migrate_perf_db  # noqa: E402
from luthien_proxy.perf.seeding import seed_sessions  # noqa: E402
from luthien_proxy.utils.db_sqlite import parse_sqlite_url  # noqa: E402

EVIDENCE_DIR = _REPO_ROOT / ".sisyphus" / "evidence"
OUTPUT_PATH = EVIDENCE_DIR / "baseline-query-plans.md"

# ── Queries ────────────────────────────────────────────────────────────────
# Exact SQL extracted from source (adapted: $N → ? for sqlite3, no f-string
# interpolation — using the hot-path / no-user-filter variant).
#
# Source: src/luthien_proxy/history/service.py (_fetch_session_list_sqlite)
SESSION_LIST_SQL = """\
SELECT
    ce.session_id,
    MIN(ce.created_at) as first_ts,
    MAX(ce.created_at) as last_ts,
    COUNT(*) as total_events,
    COUNT(DISTINCT ce.call_id) as turn_count,
    SUM(CASE
        WHEN ce.event_type LIKE 'policy.%'
        AND ce.event_type NOT LIKE 'policy.%judge.evaluation%'
        THEN 1 ELSE 0
    END) as policy_interventions
FROM conversation_events ce
WHERE ce.session_id IS NOT NULL
GROUP BY ce.session_id
ORDER BY last_ts DESC
LIMIT ? OFFSET ?\
"""

# Source: src/luthien_proxy/history/service.py (fetch_session_detail)
SESSION_DETAIL_SQL = """\
SELECT call_id, event_type, payload, created_at
FROM conversation_events
WHERE session_id = ?
ORDER BY created_at ASC\
"""

# Source: src/luthien_proxy/debug/service.py (fetch_recent_calls)
RECENT_CALLS_SQL = """\
SELECT
    call_id,
    COUNT(*) as event_count,
    MAX(created_at) as latest,
    MAX(session_id) as session_id
FROM conversation_events
GROUP BY call_id
ORDER BY latest DESC
LIMIT ?\
"""

QUERIES: list[tuple[str, str, tuple[object, ...]]] = [
    ("session_list", SESSION_LIST_SQL, (50, 0)),
    ("session_detail", SESSION_DETAIL_SQL, ("placeholder-session-id",)),
    ("recent_calls", RECENT_CALLS_SQL, (50,)),
]


def get_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def format_explain_plan(rows: list[tuple[int, int, int, str]]) -> str:
    """Format EXPLAIN QUERY PLAN rows as a tree.

    SQLite EXPLAIN QUERY PLAN returns (id, parent, notused, detail).
    We indent based on parent depth to show the nested structure.
    """
    if not rows:
        return "(no plan output)"
    id_to_depth: dict[int, int] = {0: -1}
    lines = []
    for row in rows:
        row_id, parent_id, _notused, detail = row[0], row[1], row[2], row[3]
        parent_depth = id_to_depth.get(parent_id, -1)
        depth = parent_depth + 1
        id_to_depth[row_id] = depth
        indent = "    " * depth
        connector = "`--" if depth > 0 else ""
        lines.append(f"{indent}{connector}{detail}")
    return "\n".join(lines)


def ensure_no_dev_db_in_env() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        return
    try:
        ensure_perf_isolation(database_url)
    except RuntimeError as e:
        # ensure_perf_isolation message always contains "isolation"
        print(f"isolation refuse: DATABASE_URL is set to the dev database.\n{e}")
        sys.exit(1)


def explain_sqlite(db_path: str) -> None:
    # Ensure migrations are applied (idempotent)
    print("Applying migrations...", file=sys.stderr)
    migrate_perf_db("sqlite")

    conn = sqlite3.connect(db_path)
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0]
        session_count = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM conversation_events WHERE session_id IS NOT NULL"
        ).fetchone()[0]

        if row_count == 0:
            print("Perf DB is empty — seeding with tier=100...", file=sys.stderr)
            conn.close()
            seed_sessions("sqlite", tier=100)
            conn = sqlite3.connect(db_path)
            row_count = conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0]
            session_count = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM conversation_events WHERE session_id IS NOT NULL"
            ).fetchone()[0]

        print(f"DB has {row_count} events, {session_count} sessions.", file=sys.stderr)

        git_sha = get_git_sha()
        timestamp = datetime.now(timezone.utc).isoformat()

        sections: list[str] = []
        sections.append("---")
        sections.append(f"git_sha: {git_sha}")
        sections.append(f"timestamp: {timestamp}")
        sections.append("backend: sqlite")
        sections.append(f"row_count: {row_count}")
        sections.append(f"session_count: {session_count}")
        sections.append("---")
        sections.append("")

        for name, sql, params in QUERIES:
            print(f"Running EXPLAIN QUERY PLAN for {name}...", file=sys.stderr)
            sections.append(f"## Query: {name}")
            sections.append("")
            sections.append("### SQL")
            sections.append("")
            sections.append("```sql")
            sections.append(sql)
            sections.append("```")
            sections.append("")
            sections.append("### EXPLAIN QUERY PLAN")
            sections.append("")
            sections.append("```")
            try:
                rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
                sections.append(format_explain_plan(rows))
            except sqlite3.OperationalError as e:
                sections.append(f"ERROR: {e}")
            sections.append("```")
            sections.append("")

        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text("\n".join(sections) + "\n", encoding="utf-8")
        print(f"Written: {OUTPUT_PATH}", file=sys.stderr)

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture EXPLAIN QUERY PLAN for slow queries against the perf DB.")
    parser.add_argument(
        "--backend",
        choices=["sqlite", "postgres"],
        required=True,
        help="Database backend to use.",
    )
    args = parser.parse_args()

    ensure_no_dev_db_in_env()

    try:
        url = get_perf_db_url(args.backend)
    except RuntimeError as e:
        print(f"isolation refuse: {e}")
        sys.exit(1)

    if args.backend == "sqlite":
        db_path = parse_sqlite_url(url)
        explain_sqlite(db_path)
    else:
        print("SKIPPED: Postgres backend not available in this environment.", file=sys.stderr)
        print("# SKIPPED: Postgres not available", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
