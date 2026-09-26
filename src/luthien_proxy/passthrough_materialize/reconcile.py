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
    GROUP BY request_logs.transaction_id
    ORDER BY MIN(request_logs.started_at), request_logs.transaction_id
    LIMIT $2
"""


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
            case MaterializationFailed():
                failed += 1
            case unreachable:
                assert_never(unreachable)

    return ReconcileStats(
        materialized=materialized,
        already_materialized=already_materialized,
        skipped_ineligible=skipped_ineligible,
        failed=failed,
    )


def _transaction_id(row: Mapping[str, object]) -> str:
    transaction_id = row["transaction_id"]
    if isinstance(transaction_id, str):
        return transaction_id
    raise TypeError("reconciliation query returned a non-string transaction_id")


__all__ = ["reconcile_passthrough"]
