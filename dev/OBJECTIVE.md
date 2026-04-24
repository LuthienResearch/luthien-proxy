# Objective: Fix SQLite translator positional-arg reuse

## Symptom
`POST /api/admin/credentials` returns 500 on SQLite-backed dev setups (surfaced
by PR #598). The new server-credentials admin UI can create/update nothing.

## Root cause
`CredentialStore.put` writes `created_at` and `updated_at` with
`VALUES (..., $8, $8)` — reusing positional arg $8 twice. asyncpg/Postgres
accepts this. The SQLite translator in `src/luthien_proxy/utils/db_sqlite.py`
rewrites every `$N` to `?` independently, leaving 9 `?` placeholders against 8
bindings → `sqlite3.ProgrammingError`.

## Fix
Translator-level (option b): rewrite `_translate_params` so each `$N`
occurrence expands to one `?` and the args tuple is reordered to match, so that
any SQL valid on Postgres works on SQLite.

## Acceptance
- Regression unit test exercising `CredentialStore.put` → `get` against a real
  SQLite backend (with migration applied) passes.
- Translator unit tests cover positional reuse, out-of-order reuse, and
  interleaved reuse.
- `dev_checks.sh` passes.
- Manual: `POST /api/admin/credentials` with admin token returns 200, persisted
  row is readable via GET and deletable.
