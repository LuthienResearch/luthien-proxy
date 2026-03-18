# Migrations

## PostgreSQL migrations

Each `NNN_*.sql` file is a migration applied by the Docker migrations container.
The gateway validates on startup that all local files have been applied (via `check_migrations`).

## Keeping SQLite in sync

`sqlite_schema.sql` is a **snapshot** of the full schema as a single file.
It is applied idempotently at startup when `DATABASE_URL=sqlite:///...`.

**Every time you add a PostgreSQL migration, you must also update `sqlite_schema.sql`:**

1. Add your `NNN_*.sql` migration as normal.
2. Reflect the same change in `sqlite_schema.sql`, translating PostgreSQL-specific syntax:
   - Use `INTEGER` for booleans (no native BOOLEAN type)
   - Use `TEXT` for JSON columns (no native JSONB type)
   - Use `datetime('now')` instead of `NOW()`
   - Column defaults must be SQLite-compatible (no `gen_random_uuid()` — generate UUIDs in Python)
   - No `CREATE INDEX CONCURRENTLY` — use plain `CREATE INDEX IF NOT EXISTS`
3. Use `CREATE TABLE IF NOT EXISTS` and `INSERT OR IGNORE` so the file remains idempotent.

If `sqlite_schema.sql` drifts from the PostgreSQL migrations, SQLite installs will silently
be missing columns or tables and produce confusing errors at runtime.
