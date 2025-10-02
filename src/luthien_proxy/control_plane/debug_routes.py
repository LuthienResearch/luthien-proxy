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

TOOL_CALL_DEBUG_TYPE = "conversation:tool-call"
JUDGE_DEBUG_TYPE = "protection:llm-judge-block"


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


class ToolCallLogEntry(BaseModel):
    """Structured view of tool-call records stored in debug_logs."""

    call_id: str
    trace_id: Optional[str]
    timestamp: datetime
    stream_id: Optional[str]
    chunks_buffered: Optional[int]
    tool_calls: list[JSONObject]


class JudgeBlockEntry(BaseModel):
    """Structured view of judge decisions recorded by the protection policy."""

    call_id: str
    trace_id: Optional[str]
    timestamp: datetime
    probability: float
    explanation: str
    tool_call: JSONObject
    judge_prompt: list[JSONObject]
    judge_response_text: str
    original_request: Optional[JSONObject]
    original_response: Optional[JSONObject]
    stream_chunks: Optional[list[JSONObject]]
    blocked_response: JSONObject


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


@router.get("/api/tool-calls/logs", response_model=list[ToolCallLogEntry])
async def get_tool_call_logs(
    call_id: Optional[str] = Query(default=None, min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[ToolCallLogEntry]:
    """Return recent tool-call logs optionally filtered by call id."""
    entries = await get_debug_entries(TOOL_CALL_DEBUG_TYPE, limit=limit, pool=pool, config=config)
    filtered: list[ToolCallLogEntry] = []
    call_filter = call_id if isinstance(call_id, str) and call_id else None

    for entry in entries:
        blob = entry.jsonblob
        if not isinstance(blob, dict):
            continue
        call_identifier = blob.get("call_id")
        if not isinstance(call_identifier, str) or not call_identifier:
            continue
        if call_filter is not None and call_identifier != call_filter:
            continue

        timestamp_raw = blob.get("timestamp")
        if isinstance(timestamp_raw, str):
            try:
                timestamp = datetime.fromisoformat(timestamp_raw)
            except ValueError:
                timestamp = entry.time_created
        else:
            timestamp = entry.time_created

        stream_id = blob.get("stream_id")
        stream_value = stream_id if isinstance(stream_id, str) and stream_id else None
        chunks_value = blob.get("chunks_buffered")
        chunks_buffered = chunks_value if isinstance(chunks_value, int) else None

        tool_calls_raw = blob.get("tool_calls")
        tool_calls: list[JSONObject] = []
        if isinstance(tool_calls_raw, list):
            for item in tool_calls_raw:
                if isinstance(item, dict):
                    tool_calls.append(item)

        trace_candidate = blob.get("trace_id")
        trace_value = trace_candidate if isinstance(trace_candidate, str) and trace_candidate else None

        filtered.append(
            ToolCallLogEntry(
                call_id=call_identifier,
                trace_id=trace_value,
                timestamp=timestamp,
                stream_id=stream_value,
                chunks_buffered=chunks_buffered,
                tool_calls=tool_calls,
            )
        )

    return filtered


@router.get("/api/policy/judge", response_model=list[JudgeBlockEntry])
async def get_judge_blocks(
    trace_id: str = Query(min_length=1),
    call_id: Optional[str] = Query(default=None, min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[JudgeBlockEntry]:
    """Return judge decision records filtered by trace id (and optional call id)."""
    if config.database_url is None or pool is None:
        return []

    entries = await get_debug_entries(JUDGE_DEBUG_TYPE, limit=limit, pool=pool, config=config)
    filtered: list[JudgeBlockEntry] = []

    for entry in entries:
        blob = entry.jsonblob
        if not isinstance(blob, dict):
            continue
        blob_trace = blob.get("trace_id")
        trace_value = blob_trace if isinstance(blob_trace, str) and blob_trace else None
        if trace_value != trace_id:
            continue
        blob_call = blob.get("call_id")
        call_value = blob_call if isinstance(blob_call, str) and blob_call else None
        if call_id and call_value != call_id:
            continue
        call_identifier = call_value or (blob_call if isinstance(blob_call, str) and blob_call else "unknown")

        timestamp_raw = blob.get("timestamp")
        if isinstance(timestamp_raw, str):
            try:
                timestamp = datetime.fromisoformat(timestamp_raw)
            except ValueError:
                timestamp = entry.time_created
        else:
            timestamp = entry.time_created

        tool_call_raw = blob.get("tool_call")
        tool_call = tool_call_raw if isinstance(tool_call_raw, dict) else {}

        judge_prompt_raw = blob.get("judge_prompt")
        judge_prompt: list[JSONObject] = []
        if isinstance(judge_prompt_raw, list):
            for item in judge_prompt_raw:
                if isinstance(item, dict):
                    judge_prompt.append(item)

        stream_chunks_raw = blob.get("stream_chunks")
        stream_chunks: list[JSONObject] | None = None
        if isinstance(stream_chunks_raw, list):
            stream_chunks = []
            for chunk in stream_chunks_raw:
                if isinstance(chunk, dict):
                    stream_chunks.append(chunk)

        original_request_raw = blob.get("original_request")
        original_request = original_request_raw if isinstance(original_request_raw, dict) else None

        original_response_raw = blob.get("original_response")
        original_response = original_response_raw if isinstance(original_response_raw, dict) else None

        blocked_response_raw = blob.get("blocked_response")
        blocked_response = blocked_response_raw if isinstance(blocked_response_raw, dict) else {}

        judge_response_raw = blob.get("judge_response_text")
        if isinstance(judge_response_raw, str):
            judge_response_text = judge_response_raw
        elif judge_response_raw is not None:
            judge_response_text = json.dumps(judge_response_raw)
        else:
            judge_response_text = ""

        probability_raw = blob.get("probability")
        explanation_raw = blob.get("explanation")

        filtered.append(
            JudgeBlockEntry(
                call_id=call_identifier,
                trace_id=trace_value,
                timestamp=timestamp,
                probability=float(probability_raw) if isinstance(probability_raw, (int, float)) else 0.0,
                explanation=str(explanation_raw) if explanation_raw is not None else "",
                tool_call=tool_call,
                judge_prompt=judge_prompt,
                judge_response_text=judge_response_text,
                original_request=original_request,
                original_response=original_response,
                stream_chunks=stream_chunks,
                blocked_response=blocked_response,
            )
        )

    return filtered


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
    "ToolCallLogEntry",
    "get_debug_entries",
    "get_debug_types",
    "get_debug_page",
    "get_conversation_logs",
    "get_tool_call_logs",
    "get_judge_blocks",
    "JudgeBlockEntry",
]
