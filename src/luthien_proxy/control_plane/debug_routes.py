"""HTTP routes for querying debug log data."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig
from luthien_proxy.utils.validation import require_type

from .dependencies import get_database_pool, get_project_config

router = APIRouter()

logger = logging.getLogger(__name__)


def _parse_debug_jsonblob(raw_blob: object) -> JSONObject:
    """Decode jsonblob column to a JSON object or return structured error payload."""
    if not isinstance(raw_blob, str):
        error = TypeError("debug_logs.jsonblob must be a JSON string")
        logger.error(f"Failed to parse debug_logs.jsonblob: {error}")
        return cast(JSONObject, {"raw": raw_blob, "error": str(error)})
    try:
        parsed_blob = json.loads(raw_blob)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse debug_logs.jsonblob: {exc}")
        return cast(JSONObject, {"raw": raw_blob, "error": str(exc)})
    if not isinstance(parsed_blob, dict):
        error = TypeError("debug_logs.jsonblob must decode to a JSON object")
        logger.error(f"Failed to parse debug_logs.jsonblob: {error}")
        return cast(JSONObject, {"raw": raw_blob, "error": str(error)})
    return cast(JSONObject, parsed_blob)


class DebugEntry(BaseModel):
    """Row from debug_logs representing a single debug record."""

    id: str
    time_created: datetime
    debug_type_identifier: str
    jsonblob: JSONObject


class DebugTypeInfo(BaseModel):
    """Aggregated counts and latest timestamp per debug type."""

    debug_type_identifier: str
    count: int
    latest: datetime


class DebugPage(BaseModel):
    """A simple paginated list of debug entries."""

    items: list[DebugEntry]
    page: int
    page_size: int
    total: int


class ConversationLogEntry(BaseModel):
    """Structured view of conversation turn logs stored in debug_logs."""

    call_id: str
    trace_id: Optional[str]
    direction: str
    timestamp: datetime
    payload: JSONObject


@router.get("/api/debug/{debug_type}", response_model=list[DebugEntry])
async def get_debug_entries(
    debug_type: str,
    limit: int = Query(default=50, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[DebugEntry]:
    """Return latest debug entries for a given type (paged by limit)."""
    entries: list[DebugEntry] = []
    if config.database_url is None or pool is None:
        return entries
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE debug_type_identifier = $1
                ORDER BY time_created DESC
                LIMIT $2
                """,
                debug_type,
                limit,
            )
            for row in rows:
                jb = _parse_debug_jsonblob(row["jsonblob"])
                entries.append(
                    DebugEntry(
                        id=str(row["id"]),
                        time_created=require_type(row.get("time_created"), datetime, "time_created"),
                        debug_type_identifier=require_type(
                            row.get("debug_type_identifier"), str, "debug_type_identifier"
                        ),
                        jsonblob=jb,
                    )
                )
    except Exception as exc:
        logger.error(f"Error fetching debug logs: {exc}")
    return entries


@router.get("/api/conversation/logs", response_model=list[ConversationLogEntry])
async def get_conversation_logs(
    call_id: Optional[str] = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[ConversationLogEntry]:
    """Return recent conversation turn logs optionally filtered by call id."""
    turns: list[ConversationLogEntry] = []
    call_filter = call_id if isinstance(call_id, str) and call_id else None
    entries = await get_debug_entries("conversation:turn", limit=limit, pool=pool, config=config)
    for entry in entries:
        blob = entry.jsonblob
        if not isinstance(blob, dict):
            continue
        record_call_id = blob.get("call_id")
        if not isinstance(record_call_id, str) or not record_call_id:
            continue
        if call_filter is not None and record_call_id != call_filter:
            continue
        timestamp_value = blob.get("timestamp")
        timestamp: datetime
        if isinstance(timestamp_value, str):
            try:
                timestamp = datetime.fromisoformat(timestamp_value)
            except ValueError:
                timestamp = entry.time_created
        else:
            timestamp = entry.time_created
        direction = blob.get("direction")
        if not isinstance(direction, str) or not direction:
            direction = "unknown"
        trace_candidate = blob.get("trace_id")
        trace_id: str | None = trace_candidate if isinstance(trace_candidate, str) else None
        turns.append(
            ConversationLogEntry(
                call_id=record_call_id,
                trace_id=trace_id,
                direction=direction,
                timestamp=timestamp,
                payload=blob,
            )
        )
    return turns


@router.get("/api/debug/types", response_model=list[DebugTypeInfo])
async def get_debug_types(
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[DebugTypeInfo]:
    """Return summary of available debug types with counts."""
    types: list[DebugTypeInfo] = []
    if config.database_url is None or pool is None:
        return types
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT debug_type_identifier, COUNT(*) as count, MAX(time_created) as latest
                FROM debug_logs
                GROUP BY debug_type_identifier
                ORDER BY latest DESC
                """
            )
            for row in rows:
                types.append(
                    DebugTypeInfo(
                        debug_type_identifier=require_type(
                            row.get("debug_type_identifier"), str, "debug_type_identifier"
                        ),
                        count=require_type(row.get("count"), int, "count"),
                        latest=require_type(row.get("latest"), datetime, "latest"),
                    )
                )
    except Exception as exc:
        logger.error(f"Error fetching debug types: {exc}")
    return types


@router.get("/api/debug/{debug_type}/page", response_model=DebugPage)
async def get_debug_page(
    debug_type: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> DebugPage:
    """Return a paginated slice of debug entries for a type."""
    items: list[DebugEntry] = []
    total = 0
    if config.database_url is None or pool is None:
        return DebugPage(items=items, page=page, page_size=page_size, total=total)
    try:
        async with pool.connection() as conn:
            total_row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt FROM debug_logs WHERE debug_type_identifier = $1
                """,
                debug_type,
            )
            total = require_type(total_row["cnt"], int, "cnt") if total_row else 0
            offset = (page - 1) * page_size
            rows = await conn.fetch(
                """
                SELECT id, time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE debug_type_identifier = $1
                ORDER BY time_created DESC
                LIMIT $2 OFFSET $3
                """,
                debug_type,
                page_size,
                offset,
            )
            for row in rows:
                jb = _parse_debug_jsonblob(row["jsonblob"])
                items.append(
                    DebugEntry(
                        id=str(row["id"]),
                        time_created=require_type(row.get("time_created"), datetime, "time_created"),
                        debug_type_identifier=require_type(
                            row.get("debug_type_identifier"), str, "debug_type_identifier"
                        ),
                        jsonblob=jb,
                    )
                )
    except Exception as exc:
        logger.error(f"Error fetching debug page: {exc}")
    return DebugPage(items=items, page=page, page_size=page_size, total=total)


__all__ = [
    "router",
    "DebugEntry",
    "DebugTypeInfo",
    "DebugPage",
    "ConversationLogEntry",
    "get_debug_entries",
    "get_debug_types",
    "get_debug_page",
    "get_conversation_logs",
]
