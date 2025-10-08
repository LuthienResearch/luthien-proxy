"""Database helpers for conversation tracing."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Mapping, Optional, cast

from fastapi import HTTPException

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig
from luthien_proxy.utils.validation import require_type

from .models import TraceEntry
from .utils import extract_post_time_ns_from_any


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
