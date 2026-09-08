-- ABOUTME: One-time backfill normalizing legacy space-separated SQLite
-- ABOUTME: datetime binds to the ISO-8601 "T" separator used everywhere else.
--
-- Before the fix in #806, a raw `datetime` bind fell through to stdlib
-- sqlite3's legacy adapter, which serializes with `isoformat(" ")` (a space
-- separator) instead of the "T" separator the app uses for `.isoformat()`
-- string binds. Rows written before that fix carry values like
-- "2026-07-11 10:00:00+00:00" instead of "2026-07-11T10:00:00+00:00".
--
-- A mixed-format column breaks lexical comparisons: a space (0x20) sorts
-- before "T" (0x54), so a legacy same-day row always compares as "earlier"
-- than a same-day cutoff/bound in the new "T" form, regardless of actual
-- time-of-day. Left unmigrated, this would make retention purge/archive
-- cutoffs (retention/purger.py, retention/archiver.py) sweep up legacy rows
-- before their real retention window elapses, and would let a stale
-- session_summaries.last_seen/first_seen (observability/session_summary.py)
-- ratchet backward on the next event for that session.
--
-- request_logs.started_at/completed_at are written through `to_timestamp(?)`
-- (db_sqlite.py's `_translate_params`), not a raw `datetime` bind, so this
-- backfill also covers them; the matching write-path fix (translating to a
-- "T"-separated string, not just SQLite's native space-separated
-- `datetime(?, 'unixepoch')`) lives in `_translate_params` alongside this
-- migration so old and new request_logs rows compare consistently against
-- the `after`/`before` filters in request_log/service.py.
--
-- The LIKE pattern only matches the legacy "YYYY-MM-DD HH:MM:SS..." shape
-- (a literal space at position 11), so already-"T" values and NULLs are
-- left untouched; this is safe to run more than once.
UPDATE conversation_calls
SET created_at = REPLACE(created_at, ' ', 'T')
WHERE created_at LIKE '____-__-__ __:__:__%';

UPDATE conversation_events
SET created_at = REPLACE(created_at, ' ', 'T')
WHERE created_at LIKE '____-__-__ __:__:__%';

UPDATE session_summaries
SET first_seen = REPLACE(first_seen, ' ', 'T')
WHERE first_seen LIKE '____-__-__ __:__:__%';

UPDATE session_summaries
SET last_seen = REPLACE(last_seen, ' ', 'T')
WHERE last_seen LIKE '____-__-__ __:__:__%';

UPDATE request_logs
SET started_at = REPLACE(started_at, ' ', 'T')
WHERE started_at LIKE '____-__-__ __:__:__%';

UPDATE request_logs
SET completed_at = REPLACE(completed_at, ' ', 'T')
WHERE completed_at LIKE '____-__-__ __:__:__%';
