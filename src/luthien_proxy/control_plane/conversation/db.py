"""Database helpers for conversation tracing."""

from __future__ import annotations

import json
from typing import Mapping, Optional, cast

from fastapi import HTTPException

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig

from .models import TraceEntry
from .utils import json_safe


def parse_jsonblob(raw: object) -> JSONObject:
    """Deserialize a debug log JSON blob into a dictionary."""
    if isinstance(raw, dict):
        return cast(JSONObject, raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return cast(JSONObject, parsed)
            return {"raw": raw}
        except Exception:
            return {"raw": json_safe(raw)}
    return {"raw": json_safe(raw)}


def extract_post_ns(jb: JSONObject) -> Optional[int]:
    """Extract `post_time_ns` from a log payload when present."""
    payload = jb.get("payload")
    if not isinstance(payload, dict):
        return None
    ns = payload.get("post_time_ns")
    if isinstance(ns, int):
        return ns
    if isinstance(ns, float):
        return int(ns)
    return None


def _row_to_trace_entry(row: Mapping[str, object]) -> TraceEntry:
    jb = parse_jsonblob(row["jsonblob"])
    return TraceEntry(
        time=row["time_created"],
        post_time_ns=extract_post_ns(jb),
        hook=jb.get("hook"),
        debug_type=row["debug_type_identifier"],
        payload=jb,
    )


async def fetch_trace_entries(
    call_id: str,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[TraceEntry]:
    """Load all debug log entries recorded for a call ID."""
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for trace lookups")

    entries: list[TraceEntry] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE jsonblob->>'litellm_call_id' = $1
                ORDER BY time_created ASC
                """,
                call_id,
            )
            for row in rows:
                entries.append(_row_to_trace_entry(row))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace_error: {exc}")

    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return entries


async def fetch_trace_entries_by_trace(
    trace_id: str,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[TraceEntry]:
    """Load all debug log entries recorded for a trace ID."""
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for trace lookups")

    entries: list[TraceEntry] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE COALESCE(
                    jsonblob->>'litellm_trace_id',
                    jsonblob->'payload'->'request_data'->>'litellm_trace_id',
                    jsonblob->'payload'->'data'->>'litellm_trace_id'
                ) = $1
                ORDER BY time_created ASC
                """,
                trace_id,
            )
            for row in rows:
                entries.append(_row_to_trace_entry(row))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace_error: {exc}")

    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return entries


__all__ = ["fetch_trace_entries", "fetch_trace_entries_by_trace", "parse_jsonblob", "extract_post_ns"]
