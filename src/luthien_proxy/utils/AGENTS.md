# Utils Guide

## Scope

- This directory holds shared infrastructure for database access, SQLite fallback, Redis, caches, constants, and migration checks.
- Changes here often affect multiple subsystems at once: auth, history, policy cache, request logs, and startup.

## Files that matter

| File | Why it matters |
| --- | --- |
| `db.py` | backend-agnostic pool/connection protocol and lazy pool creation |
| `db_sqlite.py` | SQLite shim that translates asyncpg-style SQL patterns |
| `migration_check.py` | startup schema checks and SQLite migration application |
| `credential_cache.py` | in-process vs Redis credential validation cache |
| `policy_cache.py` | shared DB-backed cache with PG/SQLite support |
| `redis_client.py` | Redis wiring used outside local-mode fallbacks |

## Local rules

- Prefer one shared query path with small dialect branches.
- Use SQLite translation helpers where possible instead of maintaining separate full SQL implementations.
- Branch on `db_pool.is_sqlite` only when semantics genuinely differ.
- Keep local-mode fallbacks working: empty `REDIS_URL` means in-process publisher/cache, and SQLite is a first-class dev path.

## Database-specific invariants

- `DatabasePool` is lazy and backend-detecting; do not bypass it with ad hoc connection creation.
- `parse_db_ts()` is the normalization point for timestamp values coming back as `datetime` vs ISO strings.
- SQLite support depends on asyncpg-style placeholders and selected PG syntax being translated in `db_sqlite.py`; preserve that contract.
- Migration helpers assume mirrored Postgres/SQLite migration numbering.

## Common traps

- Writing Postgres-only SQL into shared helpers when the SQLite shim could have handled it.
- Duplicating entire query implementations instead of isolating the actual dialect difference.
- Adding cache or DB utilities without thinking through both local-mode and Redis/Postgres deployments.
- Reaching into private state on `DatabasePool` in tests instead of using its public API.

## Verification targets

- Unit tests for helper behavior and translation logic.
- Integration tests for migration sync or shared DB helpers when schema interaction changes.
- `sqlite_e2e` coverage whenever a utility change could break dockerless local mode.
- Run `./scripts/dev_checks.sh` before pushing infra changes; these files are touched by many subsystems.
