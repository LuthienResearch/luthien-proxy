"""Quick helper to inspect debug_logs for a given litellm_call_id."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg


def load_env_file(env_path: Path) -> None:
    """Load simple KEY=VALUE pairs from a .env file into os.environ."""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        cleaned = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, cleaned)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect debug_logs records.")
    parser.add_argument("--call-id", help="litellm_call_id to filter on", default=None)
    parser.add_argument("--debug-type", help="debug_type_identifier filter", default=None)
    parser.add_argument("--limit", type=int, default=50, help="maximum rows to return")
    parser.add_argument(
        "--host",
        help="override host in DATABASE_URL (useful when running outside docker)",
        default=None,
    )
    parser.add_argument(
        "--ascending",
        action="store_true",
        help="present rows in chronological order (default: newest first)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="show only id/time/type and top-level payload keys",
    )
    parser.add_argument(
        "--list-types",
        action="store_true",
        help="list debug types matching filters instead of full rows",
    )
    return parser.parse_args()


async def fetch_logs(
    db_url: str, call_id: str | None, debug_type: str | None, limit: int, ascending: bool
) -> list[asyncpg.Record]:
    order_clause = "ASC" if ascending else "DESC"
    query = f"""
        SELECT id, time_created, debug_type_identifier, jsonblob
        FROM debug_logs
        WHERE ($1::text IS NULL OR jsonblob->>'litellm_call_id' = $1)
          AND ($2::text IS NULL OR debug_type_identifier = $2)
        ORDER BY time_created {order_clause}
        LIMIT $3
    """
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(query, call_id, debug_type, limit)
    finally:
        await conn.close()
    return rows


def _load_payload(raw_payload: Any) -> Any:
    if isinstance(raw_payload, str):
        try:
            return json.loads(raw_payload)
        except json.JSONDecodeError:
            return raw_payload
    return raw_payload


def format_row(row: asyncpg.Record, summary: bool) -> str:
    payload: Any = row["jsonblob"]
    payload = _load_payload(payload)
    if summary:
        key_summary = ""
        if isinstance(payload, dict):
            key_summary = ", ".join(sorted(payload.keys()))
        return f"id={row['id']} time={row['time_created']} type={row['debug_type_identifier']} keys=[{key_summary}]"
    formatted_payload = json.dumps(payload, indent=2, sort_keys=True, default=str)
    return (
        f"id={row['id']} time={row['time_created']} type={row['debug_type_identifier']}\npayload={formatted_payload}\n"
    )


def override_db_host(db_url: str, new_host: str) -> str:
    parsed = urlparse(db_url)
    if not new_host:
        return db_url
    username = parsed.username or ""
    password = parsed.password
    auth = ""
    if username:
        auth = username
        if password is not None:
            auth += f":{password}"
        auth += "@"
    port = parsed.port
    port_segment = f":{port}" if port is not None else ""
    new_netloc = f"{auth}{new_host}{port_segment}"
    return urlunparse(parsed._replace(netloc=new_netloc))


async def list_debug_types(db_url: str, call_id: str | None) -> list[asyncpg.Record]:
    query = """
        SELECT debug_type_identifier, COUNT(*) AS count
        FROM debug_logs
        WHERE ($1::text IS NULL OR jsonblob->>'litellm_call_id' = $1)
        GROUP BY debug_type_identifier
        ORDER BY count DESC
    """
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(query, call_id)
    finally:
        await conn.close()
    return rows


async def async_main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    load_env_file(repo_root / ".env")
    args = parse_args()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL must be set (check .env)")
    if args.host:
        db_url = override_db_host(db_url, args.host)

    if args.list_types:
        rows = await list_debug_types(db_url, args.call_id)
        if not rows:
            print("(no rows found)")
            return 0
        for row in rows:
            print(f"{row['debug_type_identifier']}: {row['count']}")
        return 0

    rows = await fetch_logs(db_url, args.call_id, args.debug_type, args.limit, args.ascending)
    if not rows:
        print("(no rows found)")
        return 0
    for row in rows:
        print(format_row(row, args.summary))
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(async_main())
    except KeyboardInterrupt:
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
