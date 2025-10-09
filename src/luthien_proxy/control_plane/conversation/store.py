"""Persistence helpers for conversation events."""

from __future__ import annotations

import json
from typing import Sequence

from luthien_proxy.control_plane.conversation.models import ConversationEvent
from luthien_proxy.utils import db


async def record_conversation_events(
    pool: db.DatabasePool | None,
    events: Sequence[ConversationEvent],
) -> None:
    """Persist the given events into the conversation tables."""
    if pool is None or not events:
        return

    async with pool.connection() as conn:
        for event in events:
            await _ensure_call_row(conn, event)

            if event.event_type == "request":
                await _apply_request_event(conn, event)
            elif event.event_type == "response":
                await _apply_response_event(conn, event)

            await _insert_event_row(conn, event)


async def _ensure_call_row(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Ensure a row exists for the call id."""
    await conn.execute(
        """
        INSERT INTO conversation_calls (call_id, created_at)
        VALUES ($1, $2)
        ON CONFLICT (call_id) DO NOTHING
        """,
        event.call_id,
        event.timestamp,
    )


async def _apply_request_event(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Update call metadata from request event."""
    payload = event.payload
    model_name = payload.get("model")

    await conn.execute(
        """
        UPDATE conversation_calls
        SET model_name = COALESCE(model_name, $2),
            status = 'started',
            created_at = LEAST(created_at, $3)
        WHERE call_id = $1
        """,
        event.call_id,
        model_name if isinstance(model_name, str) else None,
        event.timestamp,
    )


async def _apply_response_event(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Update call metadata from response event."""
    payload = event.payload
    status = str(payload.get("status", "success"))

    await conn.execute(
        """
        UPDATE conversation_calls
        SET status = $2,
            completed_at = COALESCE(completed_at, $3)
        WHERE call_id = $1
        """,
        event.call_id,
        status,
        event.timestamp,
    )


async def _insert_event_row(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Insert event row into conversation_events."""
    await conn.execute(
        """
        INSERT INTO conversation_events (
            call_id,
            event_type,
            sequence,
            payload,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5)
        """,
        event.call_id,
        event.event_type,
        int(event.sequence),
        json.dumps(event.payload),
        event.timestamp,
    )


__all__ = ["record_conversation_events"]
