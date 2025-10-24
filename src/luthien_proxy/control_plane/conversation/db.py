"""Database helpers for conversation queries."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Mapping, Optional, Sequence

from fastapi import HTTPException

from luthien_proxy.utils import db
from luthien_proxy.utils.validation import require_type

from .models import CallIdInfo, ConversationEvent

logger = logging.getLogger(__name__)


async def load_events_for_call(
    call_id: str,
    pool: Optional[db.DatabasePool],
    database_url: Optional[str] = None,
) -> list[ConversationEvent]:
    """Load conversation events for a single call."""
    if database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for conversation lookups")

    events: list[ConversationEvent] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT call_id,
                       event_type,
                       sequence,
                       payload,
                       created_at
                FROM conversation_events
                WHERE call_id = $1
                ORDER BY sequence ASC
                """,
                call_id,
            )
            events = _rows_to_events(rows)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conversation_events_error: {exc}")
    return events


async def load_recent_calls(
    limit: int,
    pool: Optional[db.DatabasePool],
    database_url: Optional[str] = None,
) -> list[CallIdInfo]:
    """Return recent calls recorded in conversation tables."""
    if database_url is None or pool is None:
        return []

    results: list[CallIdInfo] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT c.call_id,
                       COALESCE(stats.event_count, 0) AS event_count,
                       COALESCE(c.completed_at, c.created_at) AS latest
                FROM conversation_calls c
                LEFT JOIN (
                    SELECT call_id, COUNT(*) AS event_count
                    FROM conversation_events
                    GROUP BY call_id
                ) stats ON stats.call_id = c.call_id
                ORDER BY latest DESC NULLS LAST
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                call_id_val = require_type(row.get("call_id"), str, "call_id")
                count_val = require_type(row.get("event_count"), int, "event_count")
                latest_val = require_type(row.get("latest"), datetime, "latest")
                results.append(CallIdInfo(call_id=call_id_val, count=count_val, latest=latest_val))
    except Exception as exc:
        logger.error("Failed to load recent calls: %s", exc)
        return []
    return results


def _rows_to_events(rows: Sequence[Mapping[str, object]]) -> list[ConversationEvent]:
    """Convert database rows to ConversationEvent objects."""
    events: list[ConversationEvent] = []
    for row in rows:
        call_id_val = require_type(row.get("call_id"), str, "call_id")
        event_type = require_type(row.get("event_type"), str, "event_type")

        payload_obj = row.get("payload")
        if isinstance(payload_obj, str):
            try:
                payload_obj = json.loads(payload_obj)
            except json.JSONDecodeError:
                payload_obj = {}
        if isinstance(payload_obj, Mapping):
            payload = payload_obj
        else:
            payload = {}

        sequence_raw = row.get("sequence")
        if isinstance(sequence_raw, int):
            sequence_val = sequence_raw
        elif isinstance(sequence_raw, float):
            sequence_val = int(sequence_raw)
        else:
            created_at = require_type(row.get("created_at"), datetime, "created_at")
            sequence_val = int(created_at.timestamp() * 1_000_000_000)

        created_at_val = require_type(row.get("created_at"), datetime, "created_at")

        events.append(
            ConversationEvent(
                call_id=call_id_val,
                trace_id=None,  # No longer used
                event_type=event_type,  # type: ignore[arg-type]
                sequence=sequence_val,
                timestamp=created_at_val,
                hook="",  # Not stored in new schema
                payload=payload,  # type: ignore[arg-type]
            )
        )
    return events


__all__ = [
    "load_events_for_call",
    "load_recent_calls",
]
