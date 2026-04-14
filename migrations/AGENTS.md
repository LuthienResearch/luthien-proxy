# Migrations

## Structure

Migrations live in two directories with matched numbering:

- `postgres/` — PostgreSQL migrations (applied by Docker container or CI)
- `sqlite/` — SQLite migrations (applied in-process at startup)

## Adding a Migration

Every migration needs BOTH a Postgres and SQLite file with matched numbering.

1. Pick the next number: `ls migrations/postgres/` to see current highest. Use zero-padded 3-digit prefixes (e.g., `010`, not `10`). Note: `008` is used by two historical migrations — do not reuse prefixes.
2. Create `migrations/postgres/NNN_description.sql` with Postgres-native DDL.
3. Create `migrations/sqlite/NNN_description.sql` with SQLite-compatible DDL.
4. Copy the SQLite file: `cp migrations/sqlite/NNN_*.sql src/luthien_proxy/utils/sqlite_migrations/`
5. Run: `uv run pytest tests/luthien_proxy/integration_tests/test_migration_sync.py -v -m integration`
6. Commit all three files together.

## Type Translation (Postgres -> SQLite)

| Postgres | SQLite | Notes |
|----------|--------|-------|
| `SERIAL` | `INTEGER PRIMARY KEY` | |
| `UUID` | `TEXT` | Generate UUIDs in Python, not as column default |
| `BOOLEAN` / `BOOL` | `INTEGER` | 0/1 instead of true/false |
| `JSONB` / `JSON` | `TEXT` | Stored as JSON string |
| `TIMESTAMP` / `TIMESTAMPTZ` | `TEXT` | ISO-8601 strings |
| `DOUBLE PRECISION` | `REAL` | |
| `NOW()` | `datetime('now')` | Wrap in parentheses for DEFAULT: `DEFAULT (datetime('now'))` |
| `gen_random_uuid()` | omit | Handle in Python (migration runner does this for telemetry) |
| `id::text` | `CAST(id AS TEXT)` | |
| `ON CONFLICT (id) DO NOTHING` | `ON CONFLICT DO NOTHING` or `INSERT OR IGNORE` | |

## What to OMIT in SQLite migrations

- `CREATE EXTENSION` statements
- `COMMENT ON` statements
- `DO $$ ... $$` PL/pgSQL blocks
- `GRANT` / `REVOKE` statements
- `CREATE INDEX CONCURRENTLY` (use plain `CREATE INDEX IF NOT EXISTS`)

## SQLite Migration File Constraints

- Use only `--` line comments (no `/* */` block comments — the runner doesn't handle them)
- Do not use semicolons inside string literals (the runner splits on `;` naively)

## SQLite ALTER TABLE Limitations

- `ALTER TABLE ADD COLUMN` works (Python 3.13+ ships SQLite 3.45+)
- `ALTER TABLE DROP COLUMN` works (SQLite 3.35+, available in Python 3.13+)
- `ALTER TABLE ALTER COLUMN` does NOT work — to change a column type or default, recreate the table
- `ALTER TABLE ADD CONSTRAINT` does NOT work — constraints must be in the CREATE TABLE

## Example

**Postgres** (`migrations/postgres/010_add_foo.sql`):

```sql
CREATE TABLE IF NOT EXISTS foo (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_foo_name ON foo(name);
COMMENT ON TABLE foo IS 'Example table';
```

**SQLite** (`migrations/sqlite/010_add_foo.sql`):

```sql
CREATE TABLE IF NOT EXISTS foo (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_foo_name ON foo(name);
```
