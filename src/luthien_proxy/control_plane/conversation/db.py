"""Database helpers for conversation tracing."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Mapping, Optional, Sequence, cast

from fastapi import HTTPException

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig
from luthien_proxy.utils.validation import require_type

from .models import CallIdInfo, ConversationEvent, TraceEntry, TraceInfo
from .utils import extract_post_time_ns_from_any

logger = logging.getLogger(__name__)


def _optional_str(value: object) -> Optional[str]:
    """Return *value* when it is a non-empty string, else None."""
    if isinstance(value, str) and value:
        return value
    return None


def extract_post_ns(jb: JSONObject) -> Optional[int]:
    """Extract `post_time_ns` from a log payload when present."""
    ns = extract_post_time_ns_from_any(jb)
    if ns is not None:
        return ns
    return None


def _row_to_trace_entry(row: Mapping[str, object]) -> TraceEntry:
    raw_blob = str(row["jsonblob"])
    try:
        parsed_blob = cast(dict, json.loads(raw_blob))
    except (TypeError, json.JSONDecodeError) as exc:
        raise TypeError(f"debug_logs.jsonblob is not a valid json string: {raw_blob}") from exc
    if not isinstance(parsed_blob, dict):
        raise TypeError(f"debug_logs.jsonblob must decode to a JSON mapping; got {type(parsed_blob)!r}")
    time_created = require_type(row.get("time_created"), datetime, "time_created")
    debug_identifier = row.get("debug_type_identifier")
    debug_type = _optional_str(debug_identifier)
    return TraceEntry(
        time=time_created,
        post_time_ns=extract_post_ns(parsed_blob),
        hook=parsed_blob.get("hook"),
        debug_type=debug_type,
        payload=parsed_blob,
    )


async def fetch_trace_entries(
    call_id: str,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[list[TraceEntry], bool]:
    """Load all debug log entries recorded for a call ID."""
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for trace lookups")

    entries: list[TraceEntry] = []
    has_more = False
    try:
        async with pool.connection() as conn:
            sql = """
                SELECT time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE jsonblob->>'litellm_call_id' = $1
                ORDER BY time_created ASC
                """
            params: list[object] = [call_id]
            if limit is not None:
                sql += " LIMIT $2 OFFSET $3"
                params.extend([limit + 1, offset])
            rows = await conn.fetch(sql, *params)
            for row in rows:
                entries.append(_row_to_trace_entry(row))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace_error: {exc}")

    if limit is not None and len(entries) > limit:
        has_more = True
        entries = entries[:limit]

    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return entries, has_more


async def fetch_trace_entries_by_trace(
    trace_id: str,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[list[TraceEntry], bool]:
    """Load all debug log entries recorded for a trace ID."""
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for trace lookups")

    entries: list[TraceEntry] = []
    has_more = False
    try:
        async with pool.connection() as conn:
            sql = """
                SELECT time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE COALESCE(
                    jsonblob->>'litellm_trace_id',
                    jsonblob->'payload'->'request_data'->>'litellm_trace_id',
                    jsonblob->'payload'->'data'->>'litellm_trace_id'
                ) = $1
                ORDER BY time_created ASC
                """
            params: list[object] = [trace_id]
            if limit is not None:
                sql += " LIMIT $2 OFFSET $3"
                params.extend([limit + 1, offset])
            rows = await conn.fetch(sql, *params)
            for row in rows:
                entries.append(_row_to_trace_entry(row))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace_error: {exc}")

    if limit is not None and len(entries) > limit:
        has_more = True
        entries = entries[:limit]

    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return entries, has_more


__all__ = ["fetch_trace_entries", "fetch_trace_entries_by_trace", "extract_post_ns"]


async def load_events_for_call(
    call_id: str,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[ConversationEvent]:
    """Load structured conversation events for a single call."""
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for conversation lookups")

    events: list[ConversationEvent] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT call_id,
                       trace_id,
                       event_type,
                       hook,
                       sequence,
                       payload,
                       created_at
                FROM conversation_events
                WHERE call_id = $1
                ORDER BY created_at ASC, sequence ASC NULLS LAST
                """,
                call_id,
            )
            events = _rows_to_events(rows)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conversation_events_error: {exc}")
    return events


async def load_events_for_trace(
    trace_id: str,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[ConversationEvent]:
    """Load structured conversation events for a trace."""
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for conversation lookups")

    events: list[ConversationEvent] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT call_id,
                       trace_id,
                       event_type,
                       hook,
                       sequence,
                       payload,
                       created_at
                FROM conversation_events
                WHERE trace_id = $1
                ORDER BY created_at ASC, sequence ASC NULLS LAST
                """,
                trace_id,
            )
            events = _rows_to_events(rows)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conversation_events_error: {exc}")
    return events


async def load_recent_calls(
    limit: int,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[CallIdInfo]:
    """Return recent calls recorded in conversation tables."""
    if config.database_url is None or pool is None:
        return []

    results: list[CallIdInfo] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT c.call_id,
                       COALESCE(stats.event_count, 0) AS event_count,
                       COALESCE(c.completed_at, c.updated_at) AS latest
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


async def load_recent_traces(
    limit: int,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[TraceInfo]:
    """Return recent trace summaries from conversation tables."""
    if config.database_url is None or pool is None:
        return []

    results: list[TraceInfo] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT c.trace_id,
                       COUNT(DISTINCT c.call_id) AS call_count,
                       COALESCE(SUM(stats.event_count), 0) AS event_count,
                       MAX(COALESCE(c.completed_at, c.updated_at)) AS latest
                FROM conversation_calls c
                LEFT JOIN (
                    SELECT call_id, COUNT(*) AS event_count
                    FROM conversation_events
                    GROUP BY call_id
                ) stats ON stats.call_id = c.call_id
                WHERE c.trace_id IS NOT NULL
                GROUP BY c.trace_id
                ORDER BY latest DESC NULLS LAST
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                trace_id_val = require_type(row.get("trace_id"), str, "trace_id")
                call_count = require_type(row.get("call_count"), int, "call_count")
                event_count = require_type(row.get("event_count"), int, "event_count")
                latest = require_type(row.get("latest"), datetime, "latest")
                results.append(
                    TraceInfo(
                        trace_id=trace_id_val,
                        call_count=call_count,
                        event_count=event_count,
                        latest=latest,
                    )
                )
    except Exception as exc:
        logger.error("Failed to load recent traces: %s", exc)
        return []
    return results


def _rows_to_events(rows: Sequence[Mapping[str, object]]) -> list[ConversationEvent]:
    events: list[ConversationEvent] = []
    for row in rows:
        call_id_val = require_type(row.get("call_id"), str, "call_id")
        event_type = require_type(row.get("event_type"), str, "event_type")
        hook = require_type(row.get("hook"), str, "hook")
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
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
        trace_id_val = row.get("trace_id")
        trace_id = trace_id_val if isinstance(trace_id_val, str) else None
        events.append(
            ConversationEvent(
                call_id=call_id_val,
                trace_id=trace_id,
                event_type=event_type,  # type: ignore[arg-type]
                sequence=sequence_val,
                timestamp=created_at_val,
                hook=hook,
                payload=payload,  # type: ignore[arg-type]
            )
        )
    return events


async def load_tool_call_records(
    limit: int,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
    *,
    call_id: str | None = None,
    trace_id: str | None = None,
) -> list[dict[str, object]]:
    """Load tool call rows from the structured conversation tables."""
    if config.database_url is None or pool is None:
        return []

    conditions: list[str] = ["e.event_type IN ('request_started', 'request_completed')"]
    params: list[object] = []

    if call_id:
        conditions.append(f"call_id = ${len(params) + 1}")
        params.append(call_id)
    if trace_id:
        conditions.append(f"trace_id = ${len(params) + 1}")
        params.append(trace_id)

    params.append(limit)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    query = f"""
        SELECT call_id,
               trace_id,
               tool_call_id,
               name,
               arguments,
               status,
               response,
               chunks_buffered,
               created_at
        FROM conversation_tool_calls
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """

    rows: Sequence[Mapping[str, object]] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(query, *params)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"tool_call_records_error: {exc}")

    records: list[dict[str, object]] = []
    for row in rows:
        call_identifier = require_type(row.get("call_id"), str, "call_id")
        timestamp = require_type(row.get("created_at"), datetime, "created_at")
        record: dict[str, object] = {
            "call_id": call_identifier,
            "trace_id": row.get("trace_id"),
            "timestamp": timestamp,
            "stream_id": row.get("tool_call_id"),
            "chunks_buffered": row.get("chunks_buffered"),
            "tool_calls": [],
        }
        tool_entry: dict[str, object] = {
            "id": row.get("tool_call_id"),
            "name": row.get("name"),
            "arguments": row.get("arguments"),
            "status": row.get("status"),
            "response": row.get("response"),
        }
        record["tool_calls"] = [tool_entry]
        records.append(record)
    return records


async def load_conversation_turns(
    limit: int,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
    *,
    call_id: str | None = None,
) -> list[dict[str, object]]:
    """Load request/response conversation turns from structured tables."""
    if config.database_url is None or pool is None:
        return []

    conditions: list[str] = []
    params: list[object] = []

    if call_id:
        conditions.append(f"e.call_id = ${len(params) + 1}")
        params.append(call_id)

    params.append(limit)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    query = f"""
        SELECT e.call_id,
               c.trace_id,
               e.event_type,
               e.payload,
               e.created_at
        FROM conversation_events e
        JOIN conversation_calls c ON c.call_id = e.call_id
        {where_clause}
        ORDER BY e.created_at DESC
        LIMIT ${len(params)}
        """

    rows: Sequence[Mapping[str, object]]
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(query, *params)
    except Exception as exc:
        logger.error("Failed to load conversation turns: %s", exc)
        return []

    records: list[dict[str, object]] = []
    for row in rows:
        call_identifier = require_type(row.get("call_id"), str, "call_id")
        direction = "request" if row.get("event_type") == "request_started" else "response"
        timestamp = require_type(row.get("created_at"), datetime, "created_at")
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        records.append(
            {
                "call_id": call_identifier,
                "trace_id": row.get("trace_id"),
                "direction": direction,
                "timestamp": timestamp,
                "payload": payload,
            }
        )
    return records
