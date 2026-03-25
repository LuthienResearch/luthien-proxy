# Database Migrations

luthien-proxy supports PostgreSQL and SQLite. Each migration exists in both dialects:

- `postgres/` — Applied by Docker migration container (production) or CI
- `sqlite/` — Applied in-process at gateway startup (dockerless/single-user)

Both directories must produce equivalent schemas. A CI test verifies this.

## Adding migrations

See `CLAUDE.md` in this directory for step-by-step instructions and type translation rules.

## How it works

**PostgreSQL:** `docker/run-migrations.sh` applies files sequentially, tracking state in `_migrations`.

**SQLite:** `migration_check.py:_apply_sqlite_migrations()` does the same at startup.

## Upgrading from snapshot-based SQLite

Older versions used a single `sqlite_schema.sql` snapshot. The migration runner auto-detects this (existing tables but empty `_migrations`) and bootstraps tracking for all migrations through 009. No manual action needed.
