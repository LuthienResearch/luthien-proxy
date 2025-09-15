#!/usr/bin/env python3

"""
Open an interactive psql session using creds from .env.

Before dropping into psql, prints quick info about key tables/columns.

Usage:
  uv run python scripts/psql.py               # uses DATABASE_URL from .env
  uv run python scripts/psql.py --db litellm  # uses LITELLM_DATABASE_URL
  uv run python scripts/psql.py --url postgresql://user:pass@host:5432/db

Fail-fast: exits non-zero if psql is not found or connection fails.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class Conn:
    user: str
    password: str | None
    host: str
    port: int
    dbname: str


def parse_env_file(path: str) -> dict[str, str]:
    vals: dict[str, str] = {}
    if not os.path.exists(path):
        return vals
    with open(path, "r") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def parse_url(url: str) -> Conn:
    p = urlparse(url)
    if p.scheme not in ("postgres", "postgresql"):
        raise SystemExit(f"Unsupported URL scheme: {p.scheme}")
    host = p.hostname or "localhost"
    # Remap docker service hostnames to localhost for host connections
    if host in {"db", "postgres", "postgresql"}:
        host = "localhost"
    return Conn(
        user=p.username or "postgres",
        password=p.password,
        host=host,
        port=p.port or 5432,
        dbname=(p.path or "/postgres").lstrip("/"),
    )


def require_psql() -> str:
    exe = shutil.which("psql")
    if not exe:
        raise SystemExit(
            "psql not found in PATH. Install it (e.g., brew install libpq && brew link --force libpq) "
            "or run via Docker: docker compose exec db psql -U <user> -d <db>"
        )
    return exe


def run_psql_once(exe: str, conn: Conn, sql: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if conn.password:
        env["PGPASSWORD"] = conn.password
    cmd = [
        exe,
        "-h",
        conn.host,
        "-p",
        str(conn.port),
        "-U",
        conn.user,
        "-d",
        conn.dbname,
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ]
    return subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)


def print_banner(conn: Conn) -> None:
    print("Connecting with:")
    print(f"  host={conn.host} port={conn.port} db={conn.dbname} user={conn.user}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db",
        choices=["luthien", "litellm"],
        default="luthien",
        help="Select DATABASE_URL from .env",
    )
    ap.add_argument("--url", help="Explicit PostgreSQL URL (overrides --db)")
    args = ap.parse_args()

    env = parse_env_file(".env")
    url = (
        args.url
        or (
            env.get("DATABASE_URL")
            if args.db == "luthien"
            else env.get("LITELLM_DATABASE_URL")
        )
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise SystemExit("No database URL found. Set --url or populate .env")

    conn = parse_url(url)

    exe = require_psql()

    print_banner(conn)

    try:
        # Print a concise snapshot
        queries = [
            (
                "Tables",
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;",
            ),
            (
                "debug_logs cols",
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='debug_logs' ORDER BY ordinal_position;",
            ),
            (
                "row counts",
                "SELECT 'debug_logs' as table, COUNT(*) FROM debug_logs;",
            ),
        ]
        for title, sql in queries:
            print(f"== {title} ==")
            res = run_psql_once(exe, conn, sql)
            print(res.stdout.strip())
            print()
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 1

    # Drop into interactive psql
    env = os.environ.copy()
    if conn.password:
        env["PGPASSWORD"] = conn.password
    cmd = [
        exe,
        "-h",
        conn.host,
        "-p",
        str(conn.port),
        "-U",
        conn.user,
        "-d",
        conn.dbname,
    ]
    print("Starting interactive psql…\n")
    os.execvpe(exe, cmd, env)


if __name__ == "__main__":
    raise SystemExit(main())
