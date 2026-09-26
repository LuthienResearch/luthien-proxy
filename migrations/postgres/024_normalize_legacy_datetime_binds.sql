-- ABOUTME: No-op counterpart to sqlite/024_normalize_legacy_datetime_binds.sql,
-- ABOUTME: kept only for the migration-prefix parity check between dialects.
--
-- The SQLite migration backfills legacy space-separated `datetime` binds
-- (stdlib sqlite3's isoformat(" ") default) to the ISO-8601 "T" separator
-- the app uses everywhere else (see #806). Postgres stores these columns as
-- native `timestamptz`: asyncpg binds a Python `datetime` directly, storage
-- is binary, and comparisons are temporal rather than lexical, so this
-- class of bug cannot occur here. Nothing to migrate.
SELECT 1;
