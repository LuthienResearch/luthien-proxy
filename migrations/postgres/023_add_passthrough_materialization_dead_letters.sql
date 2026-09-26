-- ABOUTME: Adds a dead-letter table for permanently-failed passthrough materializations
-- ABOUTME: and a partial index so the reconcile worker's eligibility query stops
-- ABOUTME: re-scanning all of request_logs and stops re-selecting dead rows forever.

-- Without this table, PassthroughReconcileWorker has no way to remember a
-- MaterializationFailed transaction across sweeps. A permanently-unparseable
-- transaction (reason e.g. missing_required_field, unsupported_variant --
-- see luthien_proxy.passthrough_materialize.reconcile._is_permanent_failure)
-- is therefore re-selected and re-fails identically on every sweep forever,
-- since it is a deterministic function of already-persisted, immutable
-- request_logs bytes. Recording it here lets the eligibility query exclude
-- it while leaving genuinely transient failures (nothing is written here for
-- those) eligible for retry on the next sweep.
CREATE TABLE IF NOT EXISTS passthrough_materialization_dead_letters (
    transaction_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- request_logs is a live, multi-GB, multi-million-row production table with
-- no index supporting the reconcile eligibility query's filter, so that
-- query ran a Parallel Seq Scan discarding essentially the whole table every
-- 300s (see PR description for a production EXPLAIN). This partial index
-- mirrors _ELIGIBLE_UNMATERIALIZED_TRANSACTIONS_SQL's WHERE clause in
-- luthien_proxy.passthrough_materialize.reconcile *exactly* (direction and
-- the four passthrough endpoint patterns): a partial index whose predicate
-- matches the query verbatim lets Postgres use it as a covering index-only
-- scan restricted to just the rows that could ever be eligible, rather than
-- indexing the ~50% of the table that is merely direction='inbound'
-- (measured locally: cost 70408->310, a ~227x reduction; see PR description).
-- If the eligibility query's endpoint list ever changes, this index's WHERE
-- clause must change with it or the planner will stop using it.
--
-- CONCURRENTLY: a plain CREATE INDEX takes a SHARE lock that blocks writes
-- (INSERTs from every in-flight request) for the full build duration;
-- CONCURRENTLY avoids that at the cost of two table scans and not running
-- inside a transaction block. `psql -f` (docker/run-migrations.sh) commits
-- each statement in this file independently, so CONCURRENTLY coexisting with
-- the CREATE TABLE above is safe -- just never wrap this file's statements
-- in an explicit BEGIN/COMMIT.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_request_logs_passthrough_eligible
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
