-- ABOUTME: Adds a dead-letter table for permanently-failed passthrough materializations
-- ABOUTME: and a partial index mirroring the Postgres migration's eligibility-query
-- ABOUTME: predicate; SQLite 3.8+ supports partial indexes via CREATE INDEX ... WHERE.

CREATE TABLE IF NOT EXISTS passthrough_materialization_dead_letters (
    transaction_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    failed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- No CONCURRENTLY (SQLite doesn't support it; this is the dockerless
-- single-user database path, not a live production table under load).
CREATE INDEX IF NOT EXISTS idx_request_logs_passthrough_eligible
    ON request_logs (started_at, transaction_id)
    WHERE direction = 'inbound'
      AND (
          endpoint IN (
              '/openai/v1/chat/completions',
              '/openai/v1/responses'
          )
          OR endpoint LIKE '/gemini/%:generateContent'
          OR endpoint LIKE '/gemini/%:streamGenerateContent'
      );
