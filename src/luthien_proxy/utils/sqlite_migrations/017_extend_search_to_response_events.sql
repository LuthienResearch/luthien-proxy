-- ABOUTME: SQLite stub for migration 017 (response event search extension)
-- ABOUTME: SQLite search is computed on-the-fly in service.py, no schema change
--
-- BACKEND DIVERGENCE: On Postgres, migration 017 extends the tsvector trigger
-- to index response events (streaming + non-streaming), so stemmed FTS applies
-- to both request and response text. On SQLite, the FTS5 table (014) only
-- indexes request events; response text is searched via an unstemmed LIKE/json_tree
-- fallback in history/service.py. This means a query like "running" will match
-- "runs" in response text on Postgres but not on SQLite.
SELECT 1
