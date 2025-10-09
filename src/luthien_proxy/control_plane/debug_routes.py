"""HTTP routes for querying debug log data."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

# Legacy conversation functions removed - these endpoints are deprecated
from luthien_proxy.control_plane.judge import load_judge_decisions, load_judge_traces
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
    timing: Optional[JSONObject]
    judge_config: Optional[JSONObject]


class JudgeTraceSummary(BaseModel):
    """Summary of a trace with judge policy applications."""

    trace_id: str
    first_seen: datetime
    last_seen: datetime
    block_count: int
    max_probability: float


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
    """Deprecated endpoint - use /api/hooks/conversation instead."""
    return []


@router.get("/api/tool-calls/logs", response_model=list[ToolCallLogEntry])
async def get_tool_call_logs(
    call_id: Optional[str] = Query(default=None, min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[ToolCallLogEntry]:
    """Deprecated endpoint - tool calls are now tracked in conversation events."""
    return []


@router.get("/api/policy/judge", response_model=list[JudgeBlockEntry])
async def get_judge_blocks(
    trace_id: str = Query(min_length=1),
    call_id: Optional[str] = Query(default=None, min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[JudgeBlockEntry]:
    """Return judge decision records filtered by trace id (and optional call id)."""
    records = await load_judge_decisions(
        trace_id=trace_id,
        call_id=call_id,
        limit=limit,
        pool=pool,
        config=config,
    )

    results: list[JudgeBlockEntry] = []
    for record in records:
        tool_call_raw = record.get("tool_call")
        tool_call = tool_call_raw if isinstance(tool_call_raw, dict) else {}

        judge_prompt_value = record.get("judge_prompt")
        judge_prompt: list[JSONObject] = []
        if isinstance(judge_prompt_value, list):
            for item in judge_prompt_value:
                if isinstance(item, dict):
                    judge_prompt.append(item)

        stream_chunks_value = record.get("stream_chunks")
        stream_chunks: list[JSONObject] | None = None
        if isinstance(stream_chunks_value, list):
            stream_chunks = [chunk for chunk in stream_chunks_value if isinstance(chunk, dict)]

        original_request = record.get("original_request") if isinstance(record.get("original_request"), dict) else None
        original_response = (
            record.get("original_response") if isinstance(record.get("original_response"), dict) else None
        )
        blocked_response = record.get("blocked_response") if isinstance(record.get("blocked_response"), dict) else {}
        timing = record.get("timing") if isinstance(record.get("timing"), dict) else None
        judge_config = record.get("judge_config") if isinstance(record.get("judge_config"), dict) else None

        judge_response_text_raw = record.get("judge_response_text")
        judge_response_text = str(judge_response_text_raw) if judge_response_text_raw is not None else ""

        probability_value = record.get("probability")
        probability = float(probability_value) if isinstance(probability_value, (int, float)) else 0.0

        explanation_raw = record.get("explanation")
        explanation = str(explanation_raw) if explanation_raw is not None else ""

        results.append(
            JudgeBlockEntry(
                call_id=require_type(record.get("call_id"), str, "call_id"),
                trace_id=record.get("trace_id"),
                timestamp=require_type(record.get("timestamp"), datetime, "timestamp"),
                probability=probability,
                explanation=explanation,
                tool_call=tool_call,
                judge_prompt=judge_prompt,
                judge_response_text=judge_response_text,
                original_request=original_request,
                original_response=original_response,
                stream_chunks=stream_chunks,
                blocked_response=blocked_response,
                timing=timing,
                judge_config=judge_config,
            )
        )

    return results


@router.get("/api/policy/judge/traces", response_model=list[JudgeTraceSummary])
async def get_judge_traces(
    limit: int = Query(default=50, ge=1, le=200),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[JudgeTraceSummary]:
    """Return list of traces with judge policy applications, sorted by most recent."""
    records = await load_judge_traces(limit=limit, pool=pool, config=config)
    return [
        JudgeTraceSummary(
            trace_id=require_type(record.get("trace_id"), str, "trace_id"),
            first_seen=require_type(record.get("first_seen"), datetime, "first_seen"),
            last_seen=require_type(record.get("last_seen"), datetime, "last_seen"),
            block_count=require_type(record.get("block_count"), int, "block_count"),
            max_probability=float(record.get("max_probability") or 0.0),
        )
        for record in records
    ]


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
