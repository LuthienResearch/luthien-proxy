"""Bounded reconciliation of raw passthrough transactions."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import assert_never

import aiosqlite
import asyncpg

from luthien_proxy.passthrough_materialize.materialize import materialize_transaction
from luthien_proxy.passthrough_materialize.materialize_types import (
    AlreadyMaterialized,
    MaterializationFailed,
    Materialized,
    ReconcileStats,
    SkippedIneligible,
)
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

# NOTE: The endpoint filter below must stay in sync with the partial index
# idx_request_logs_passthrough_eligible (migrations/postgres/023_*.sql and its
# SQLite counterpart) -- that index's WHERE clause mirrors this predicate
# verbatim so Postgres can use it as a covering index-only scan instead of a
# Parallel Seq Scan over request_logs. Changing the endpoint list here without
# updating the index's predicate (in a new migration) silently reintroduces
# the seq scan.
_ELIGIBLE_UNMATERIALIZED_TRANSACTIONS_SQL = """
    SELECT request_logs.transaction_id
    FROM request_logs
    WHERE request_logs.direction = 'inbound'
      AND (
          request_logs.endpoint IN (
              '/openai/v1/chat/completions',
              '/openai/v1/responses'
          )
          OR request_logs.endpoint LIKE '/gemini/%:generateContent'
          OR request_logs.endpoint LIKE '/gemini/%:streamGenerateContent'
      )
      AND request_logs.started_at >= COALESCE($1, request_logs.started_at)
      AND NOT EXISTS (
          SELECT 1
          FROM conversation_events
          WHERE conversation_events.call_id = request_logs.transaction_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM passthrough_materialization_dead_letters
          WHERE passthrough_materialization_dead_letters.transaction_id = request_logs.transaction_id
      )
    GROUP BY request_logs.transaction_id
    ORDER BY MIN(request_logs.started_at), request_logs.transaction_id
    LIMIT $2
"""

_RECORD_DEAD_LETTER_SQL = """
    INSERT INTO passthrough_materialization_dead_letters (transaction_id, reason)
    VALUES ($1, $2)
    ON CONFLICT (transaction_id) DO NOTHING
"""

# Every MaterializationFailed reason except "missing_request_logs" is raised only
# after read_raw_transaction/parse_captured_transaction *successfully* fetched
# request_logs bytes and then found them unparseable or invalid for the matched
# endpoint (see PassthroughNormalizeReason and the _InvalidRequestLog family in
# materialize_read.py). request_logs rows are never updated after insert, so
# retrying one of those failures without a code change reproduces the identical
# outcome forever -- they are permanent. "missing_request_logs" is the one
# reason that reflects an *absence* (zero rows found for the transaction_id)
# rather than malformed content already read; treating it as permanent risks
# permanently blacklisting a transaction whose row simply had not landed yet
# (or would land on a later sweep), so it is the only reason left retryable.
_TRANSIENT_FAILURE_REASONS = frozenset({"missing_request_logs"})


def _is_permanent_failure(reason: str) -> bool:
    """Return whether a materialization failure reason will never resolve on retry."""
    return reason not in _TRANSIENT_FAILURE_REASONS


async def reconcile_passthrough(db_pool: DatabasePool, *, limit: int, since: datetime | None = None) -> ReconcileStats:
    """Materialize eligible raw transactions that do not yet have request events."""
    async with db_pool.connection() as conn:
        rows = await conn.fetch(_ELIGIBLE_UNMATERIALIZED_TRANSACTIONS_SQL, since, limit)

    materialized = 0
    already_materialized = 0
    skipped_ineligible = 0
    failed = 0
    for row in rows:
        transaction_id = _transaction_id(row)
        try:
            result = await materialize_transaction(db_pool, transaction_id)
        except (aiosqlite.Error, asyncpg.PostgresError) as error:
            logger.warning("Passthrough reconciliation transaction failed for %s: %s", transaction_id, error)
            failed += 1
            continue
        match result:
            case Materialized():
                materialized += 1
            case AlreadyMaterialized():
                already_materialized += 1
            case SkippedIneligible():
                skipped_ineligible += 1
            case MaterializationFailed() as failure:
                failed += 1
                if _is_permanent_failure(failure.reason):
                    await _record_dead_letter(db_pool, failure)
            case unreachable:
                assert_never(unreachable)

    return ReconcileStats(
        materialized=materialized,
        already_materialized=already_materialized,
        skipped_ineligible=skipped_ineligible,
        failed=failed,
    )


async def _record_dead_letter(db_pool: DatabasePool, failure: MaterializationFailed) -> None:
    """Persist a permanent materialization failure so it is not re-selected.

    A DB error here is logged and swallowed rather than propagated: the sweep's
    other transactions must not be aborted because one dead-letter write failed.
    Not recording it just means this transaction is retried (and, if still
    permanently broken, re-attempted to be dead-lettered) on the next sweep --
    safe, if wasteful, rather than silently losing the failure.
    """
    try:
        async with db_pool.connection() as conn:
            await conn.execute(_RECORD_DEAD_LETTER_SQL, failure.transaction_id, failure.reason)
    except (aiosqlite.Error, asyncpg.PostgresError):
        logger.exception(
            "Failed to record passthrough dead letter for %s (reason=%s); it will be retried next sweep",
            failure.transaction_id,
            failure.reason,
        )


def _transaction_id(row: Mapping[str, object]) -> str:
    transaction_id = row["transaction_id"]
    if isinstance(transaction_id, str):
        return transaction_id
    raise TypeError("reconciliation query returned a non-string transaction_id")


__all__ = ["reconcile_passthrough"]
