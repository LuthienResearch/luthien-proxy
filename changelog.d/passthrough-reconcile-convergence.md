---
category: Fixes
pr: 813
---

**`PassthroughReconcileWorker` no longer runs forever with zero progress**: the
periodic reconciliation sweep had no way to remember a permanently-unparseable
passthrough transaction (`missing_required_field`, `unsupported_variant`, etc.)
across sweeps, so it re-selected and re-failed the exact same rows every
`PASSTHROUGH_MATERIALIZE_RECONCILE_INTERVAL_SECONDS` forever. Permanent
failures are now recorded in a new `passthrough_materialization_dead_letters`
table and excluded from the eligibility query; transient failures (a DB error,
or `missing_request_logs`, which could reflect a row that hasn't landed yet)
remain eligible for retry. The eligibility query's `request_logs` scan also
had no supporting index and ran a full Parallel Seq Scan on every sweep; a new
partial index mirrors the query's predicate exactly, turning it into a
covering index-only scan.
  - The worker also now stops itself once a sweep selects nothing at all,
    matching its own `PASSTHROUGH_MATERIALIZE_BACKFILL_ENABLED` config
    docstring ("one-shot ... backfill") instead of polling an empty backlog
    forever.
