"""Atomic canonical conversation persistence for passthrough captures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from luthien_proxy.observability.session_summary import update_session_summary
from luthien_proxy.passthrough_materialize.materialize_types import (
    AlreadyMaterialized,
    CanonicalTransaction,
    Materialized,
)
from luthien_proxy.passthrough_materialize.payloads import CanonicalRequestPayload, CanonicalResponsePayload
from luthien_proxy.utils.db import ConnectionProtocol, DatabasePool


@dataclass(frozen=True, slots=True)
class _EventWrite:
    event_type: str
    payload: CanonicalRequestPayload | CanonicalResponsePayload
    created_at: datetime


async def write_canonical_transaction(
    db_pool: DatabasePool, transaction: CanonicalTransaction
) -> Materialized | AlreadyMaterialized:
    """Write a complete canonical transaction or return its existing completion marker."""
    raw = transaction.captured.raw
    async with db_pool.connection() as conn:
        async with conn.transaction():
            if db_pool.is_postgres:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", raw.transaction_id)
            if await _request_event_exists(conn, raw.transaction_id):
                return AlreadyMaterialized(transaction_id=raw.transaction_id)
            await _upsert_call(conn, transaction)
            request_event = _EventWrite(
                event_type="transaction.request_recorded",
                payload=transaction.request_payload,
                created_at=transaction.request_at,
            )
            response_event = _EventWrite(
                event_type=transaction.response_payload["event_type"],
                payload=transaction.response_payload,
                created_at=transaction.response_at,
            )
            await _insert_event(conn, transaction, request_event)
            await _insert_event(conn, transaction, response_event)
            await _update_summaries(conn, transaction, request_event)
            await _update_summaries(conn, transaction, response_event)
    return Materialized(transaction_id=raw.transaction_id)


async def _request_event_exists(conn: ConnectionProtocol, transaction_id: str) -> bool:
    return (
        await conn.fetchrow(
            """
            SELECT 1 FROM conversation_events
            WHERE call_id = $1 AND event_type = 'transaction.request_recorded'
            LIMIT 1
            """,
            transaction_id,
        )
        is not None
    )


async def _upsert_call(conn: ConnectionProtocol, transaction: CanonicalTransaction) -> None:
    raw = transaction.captured.raw
    await conn.execute(
        """
        INSERT INTO conversation_calls (
            call_id, model_name, provider, status, created_at, completed_at, session_id, user_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (call_id) DO UPDATE SET
            model_name = COALESCE(conversation_calls.model_name, EXCLUDED.model_name),
            provider = COALESCE(conversation_calls.provider, EXCLUDED.provider),
            status = COALESCE(conversation_calls.status, EXCLUDED.status),
            completed_at = COALESCE(conversation_calls.completed_at, EXCLUDED.completed_at),
            session_id = COALESCE(conversation_calls.session_id, EXCLUDED.session_id),
            user_id = COALESCE(conversation_calls.user_id, EXCLUDED.user_id)
        """,
        raw.transaction_id,
        transaction.final_model,
        transaction.captured.endpoint.provider.value,
        transaction.status,
        transaction.request_at,
        transaction.response_at,
        raw.session_id,
        raw.user_id,
    )


async def _insert_event(conn: ConnectionProtocol, transaction: CanonicalTransaction, event: _EventWrite) -> None:
    raw = transaction.captured.raw
    await conn.execute(
        """
        INSERT INTO conversation_events (id, call_id, event_type, payload, created_at, session_id)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        """,
        uuid4().hex,
        raw.transaction_id,
        event.event_type,
        json.dumps(event.payload),
        event.created_at,
        raw.session_id,
    )


async def _update_summaries(conn: ConnectionProtocol, transaction: CanonicalTransaction, event: _EventWrite) -> None:
    raw = transaction.captured.raw
    if raw.session_id is not None and raw.session_id:
        await update_session_summary(
            conn,
            session_id=raw.session_id,
            event_type=event.event_type,
            data=dict(event.payload),
            user_id=raw.user_id,
            timestamp=event.created_at,
        )


__all__ = ["write_canonical_transaction"]
