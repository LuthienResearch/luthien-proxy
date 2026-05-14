# Migration Runner: CONCURRENTLY Support Audit

_Date: 2026-05-15 | Branch: perf-baseline_

## Background

`CREATE INDEX CONCURRENTLY` is a Postgres feature that builds an index without holding a lock on the table, allowing reads and writes during the build. The constraint: it **cannot run inside a transaction block**. This audit investigates whether the current migration runner can safely execute such a statement.

---

## Current behavior

### PostgreSQL runner (`docker/run-migrations.sh`)

- Applied by the `migrations` Docker service at startup; controlled by `docker compose up migrations`.
- Sequentially applies all `*.sql` files in `migrations/postgres/` in alphabetical order.
- **No `BEGIN`/`COMMIT` transaction wrapping** is added around migration files. The runner calls `psql -f "$migration"` directly:
  ```sh
  psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -f "$migration"
  ```
- `psql` defaults to autocommit mode — each statement in the file runs in its own implicit transaction unless the file itself contains explicit `BEGIN`/`COMMIT` blocks.
- The `_migrations` tracking row (`INSERT INTO _migrations`) is inserted in a **separate, subsequent `psql` invocation**, not inside the same transaction as the migration file. This means the tracking and the DDL are non-atomic: a crash between the two steps leaves schema changes applied but untracked.
- Migration state is tracked in the `_migrations` table (columns: `filename TEXT PK`, `applied_at TIMESTAMP`, `content_hash TEXT`).
- Applied-migration detection uses `SELECT COUNT(*) FROM _migrations WHERE filename = '$filename'`, checked per file before applying.
- Hash validation compares stored MD5 against local file MD5 and aborts on mismatch.

### SQLite runner (`src/luthien_proxy/utils/migration_check.py :: _apply_sqlite_migrations`)

- Runs in-process at gateway startup for dockerless/SQLite deployments.
- Uses `executescript()` to apply each `.sql` file — this method issues an implicit `COMMIT` before execution and runs all statements in the file sequentially.
- `CREATE INDEX CONCURRENTLY` is not a SQLite concept; `AGENTS.md` explicitly lists it under "What to OMIT in SQLite migrations" and directs authors to use `CREATE INDEX IF NOT EXISTS` instead.
- SQLite tracking is also done in the `_migrations` table but is written inside the same connection context (not atomic with the `executescript`, however — a mid-script crash leaves partial schema with no tracking record).

---

## Verdict

**PARTIAL**

`CREATE INDEX CONCURRENTLY` can be placed in a Postgres migration file today and will execute successfully — because the runner uses `psql -f` in autocommit mode with **no outer transaction wrapping**. The statement will not hit the "cannot run inside a transaction block" error.

However:

1. **Non-atomic tracking** — the `INSERT INTO _migrations` tracking row is a separate psql call. If it fails, the index exists on disk but the migration is untracked. A re-run will try to apply the file again; `CREATE INDEX CONCURRENTLY IF NOT EXISTS` protects against failure in that case.
2. **SQLite incompatibility** — a companion SQLite migration must use plain `CREATE INDEX IF NOT EXISTS` (standard `AGENTS.md` practice; no code change needed).
3. **No explicit guidance in runner or AGENTS.md** about CONCURRENTLY for Postgres beyond the SQLite omit rule — the assumption has been "it just works because psql is autocommit."

---

## Findings

1. **No BEGIN/COMMIT wrapping in Postgres runner.** `run-migrations.sh` calls `psql -f "$migration"` with zero explicit transaction control around migration files. psql autocommit applies.

2. **`BEGIN` in existing migrations is always PL/pgSQL, not transaction control.** Searching all postgres migration files reveals `BEGIN` only inside `$$ LANGUAGE plpgsql` function/trigger bodies (e.g., `014_add_session_search_fts.sql`, `000_init_databases.sql`). No migration wraps its DDL in a `BEGIN...COMMIT` block.

3. **Tracking INSERT is not atomic with migration application.** Lines 153–156 of `run-migrations.sh` run the migration file, then insert into `_migrations` in a second psql call. A process kill between those two calls yields applied-but-untracked state. `CREATE INDEX CONCURRENTLY IF NOT EXISTS` + idempotent DDL is the correct mitigation.

4. **SQLite runner uses `executescript()`, not raw `execute()`.** This means the entire SQL file is submitted to SQLite's native multi-statement parser in one call. It handles trigger `BEGIN...END` correctly but does not guarantee atomicity across the file; a mid-script error leaves partial schema with no `_migrations` entry.

5. **AGENTS.md already documents the SQLite handling rule.** "What to OMIT in SQLite migrations" includes `CREATE INDEX CONCURRENTLY` — use plain `CREATE INDEX IF NOT EXISTS`. This is the only dual-dialect consideration; Postgres needs no special handling beyond `IF NOT EXISTS`.

6. **Migration 006 establishes the index-in-migration pattern.** `006_add_session_id.sql` creates two partial indexes (`WHERE session_id IS NOT NULL`) with `CREATE INDEX IF NOT EXISTS`. This is the precedent: use `IF NOT EXISTS` for idempotence, and the runner handles it without transaction complications.

7. **`014_add_session_search_fts.sql` creates multiple indexes in one file.** A GIN index, a btree partial index, and an expression index are all created in a single migration file, all with `IF NOT EXISTS`. This confirms that non-trivial index migrations work fine under the current runner.

---

## Risk assessment

### If a future PR needs `CREATE INDEX CONCURRENTLY` (Postgres)

**Risk: LOW** — the runner already runs in autocommit mode. No runner changes are required.

**Smallest safe path:**

1. Postgres migration file: use `CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_name ON table(col)`.
   - `IF NOT EXISTS` handles the non-atomic tracking race condition: if the runner crashes after DDL but before tracking, the re-run skips the existing index without error.
   - Note: `CREATE INDEX CONCURRENTLY IF NOT EXISTS` requires Postgres 9.5+. Luthien targets modern Postgres; this is not a concern.
2. SQLite migration file: use plain `CREATE INDEX IF NOT EXISTS idx_name ON table(col)` (no CONCURRENTLY keyword).
3. No changes to `run-migrations.sh` or `migration_check.py` are needed.

**Residual risk:** `CREATE INDEX CONCURRENTLY` holds a share-update-exclusive lock, not a full table lock, but it does require two table scans. On a large `conversation_events` table it may run for minutes. The Docker `migrations` container has no configurable `lock_timeout`; a very large production table could cause the migration container to hang. Mitigation: document the expected index build time in the migration file comment, or run it manually outside the automated runner for very large tables.

**Out-of-scope risk (do not fix here):** The non-atomic tracking gap exists for ALL migrations, not just CONCURRENTLY ones. A proper fix would wrap both the DDL and the `INSERT INTO _migrations` in a single transaction — but that would break `CREATE INDEX CONCURRENTLY`. The correct long-term approach is to move tracking into the same psql session with `\set ON_ERROR_STOP on` and careful sequencing, but that is a separate refactor not required for this PR series.
