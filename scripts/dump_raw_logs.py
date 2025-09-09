"""
Helper script to inspect raw request/response logs for investigation.

Usage:
  uv run python scripts/dump_raw_logs.py --limit 20
  uv run python scripts/dump_raw_logs.py --id <uuid>

This avoids touching core app logic; uses the `request_logs` table populated
by LuthienPolicy (e.g., LoggingPolicy) to inspect the full kwargs payloads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg


async def fetch_by_id(conn: asyncpg.Connection, entry_id: str):
    row = await conn.fetchrow(
        """
        SELECT id, stage, call_type, request, response, created_at
        FROM request_logs
        WHERE id = $1
        """,
        entry_id,
    )
    return row


async def fetch_recent(conn: asyncpg.Connection, limit: int):
    rows = await conn.fetch(
        """
        SELECT id, stage, call_type, request, response, created_at
        FROM request_logs
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return rows


def pretty(obj):
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except Exception:
        return str(obj)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Log entry UUID to dump", default=None)
    parser.add_argument(
        "--limit", type=int, default=10, help="Number of recent rows to show"
    )
    args = parser.parse_args()

    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )
    conn = await asyncpg.connect(db_url)
    try:
        if args.id:
            row = await fetch_by_id(conn, args.id)
            if not row:
                print(f"No log found with id {args.id}")
                return
            req = json.loads(row["request"]) if row["request"] else {}
            resp = json.loads(row["response"]) if row["response"] else None
            print(
                f"id: {row['id']}  stage: {row['stage']}  call_type: {row['call_type']}  created_at: {row['created_at']}"
            )
            print("\nrequest:")
            print(pretty(req))
            print("\nresponse:")
            print(pretty(resp))
        else:
            rows = await fetch_recent(conn, args.limit)
            for r in rows:
                req = json.loads(r["request"]) if r["request"] else {}
                keys = ", ".join(list(req.keys())[:10])
                print(
                    f"id: {r['id']}  stage: {r['stage']}  call_type: {r['call_type']}  created_at: {r['created_at']}  request_keys: [{keys}]"
                )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
