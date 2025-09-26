"""HTTP routes for receiving LiteLLM hook callbacks and trace queries."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from luthien_proxy.control_plane.conversation import (
    CallIdInfo,
    ConversationMessageDiff,
    ConversationSnapshot,
    TraceConversationSnapshot,
    TraceInfo,
    TraceResponse,
    build_call_snapshots,
    build_conversation_events,
    conversation_sse_stream,
    conversation_sse_stream_by_trace,
    events_from_trace_entries,
    fetch_trace_entries,
    fetch_trace_entries_by_trace,
    json_safe,
    publish_conversation_event,
    publish_trace_conversation_event,
    strip_post_time_ns,
)
from luthien_proxy.control_plane.conversation.utils import extract_trace_id
from luthien_proxy.control_plane.utils.hooks import extract_call_id_for_hook
from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.utils import db, redis_client
from luthien_proxy.utils.project_config import ConversationStreamConfig, ProjectConfig

from .dependencies import (
    DebugLogWriter,
    get_active_policy,
    get_database_pool,
    get_debug_log_writer,
    get_hook_counter_state,
    get_project_config,
    get_conversation_rate_limiter,
    get_conversation_stream_config,
    get_redis_client,
)

from .utils.rate_limiter import RateLimiter

router = APIRouter()

logger = logging.getLogger(__name__)


async def enforce_conversation_rate_limit(
    request: Request,
    limiter: RateLimiter = Depends(get_conversation_rate_limiter),
) -> None:
    """Apply rate limiting for SSE streaming endpoints."""

    client_host = request.client.host if request.client else "unknown"
    key = f"{client_host}:{request.url.path}"
    allowed = await limiter.try_acquire(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many streaming requests, please slow down.",
        )


@router.get("/api/hooks/counters")
async def get_hook_counters(
    counters: Counter[str] = Depends(get_hook_counter_state),
) -> dict[str, int]:
    """Expose in-memory hook counters for sanity/testing scripts."""
    return dict(counters)


@router.post("/api/hooks/{hook_name}")
async def hook_generic(
    hook_name: str,
    payload: dict[str, Any],
    debug_writer: DebugLogWriter = Depends(get_debug_log_writer),
    policy: LuthienPolicy = Depends(get_active_policy),
    counters: Counter[str] = Depends(get_hook_counter_state),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
) -> Any:
    """Generic hook endpoint for any CustomLogger hook."""
    try:
        record_payload = json_safe(payload)
        stored_payload = deepcopy(record_payload)
        record = {
            "hook": hook_name,
            "payload": record_payload,
        }
        logger.debug("hook=%s payload=%s", hook_name, json.dumps(record_payload, ensure_ascii=False))
        try:
            call_id = extract_call_id_for_hook(hook_name, payload)
            if isinstance(call_id, str) and call_id:
                record["litellm_call_id"] = call_id
        except Exception:
            pass

        stored_record: dict[str, Any] = {"hook": hook_name, "payload": stored_payload}
        trace_id = extract_trace_id(payload)
        if trace_id:
            record["litellm_trace_id"] = trace_id
            stored_record["litellm_trace_id"] = trace_id
        if "litellm_call_id" in record:
            stored_record["litellm_call_id"] = record["litellm_call_id"]
        asyncio.create_task(debug_writer(f"hook:{hook_name}", stored_record))
        name = hook_name.lower()
        counters[name] += 1
        handler = cast(
            Optional[Callable[..., Awaitable[Any]]],
            getattr(policy, name, None),
        )

        handler_result = None
        if handler:
            policy_payload = strip_post_time_ns(payload)
            signature = inspect.signature(handler)
            parameters = signature.parameters
            accepts_var_kw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
            if accepts_var_kw:
                filtered_payload = policy_payload
            else:
                parameter_names = {name for name in parameters.keys() if name != "self"}
                filtered_payload = {k: v for k, v in policy_payload.items() if k in parameter_names}
            handler_result = await handler(**filtered_payload)
        final_result = handler_result if handler_result is not None else payload

        sanitized_result = json_safe(final_result)

        result_record = {
            "hook": hook_name,
            "litellm_call_id": record.get("litellm_call_id"),
            "original": stored_payload,
            "result": sanitized_result,
        }
        if trace_id:
            result_record["litellm_trace_id"] = trace_id
        asyncio.create_task(debug_writer(f"hook_result:{hook_name}", result_record))

        call_id = result_record.get("litellm_call_id")
        if isinstance(call_id, str) and call_id:
            timestamp_dt = datetime.now(timezone.utc)
            events = build_conversation_events(
                hook=hook_name,
                call_id=call_id,
                trace_id=trace_id,
                original=stored_payload,
                result=result_record["result"],
                timestamp_ns_fallback=time.time_ns(),
                timestamp=timestamp_dt,
            )
            for event in events:
                asyncio.create_task(publish_conversation_event(redis_conn, event))
                asyncio.create_task(publish_trace_conversation_event(redis_conn, event))

        return strip_post_time_ns(final_result)
    except Exception as exc:
        logger.error("hook_generic_error: %s", exc)
        raise HTTPException(status_code=500, detail=f"hook_generic_error: {exc}")


@router.get("/api/hooks/trace_by_call_id", response_model=TraceResponse)
async def trace_by_call_id(
    call_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceResponse:
    """Return ordered hook entries from debug_logs for a litellm_call_id."""
    entries = await fetch_trace_entries(call_id, pool, config)
    return TraceResponse(call_id=call_id, entries=entries)


@router.get("/api/hooks/recent_call_ids", response_model=list[CallIdInfo])
async def recent_call_ids(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[CallIdInfo]:
    """Return recent call IDs observed in debug logs with usage counts."""
    out: list[CallIdInfo] = []
    if config.database_url is None or pool is None:
        return out
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT jsonblob->>'litellm_call_id' as cid,
                       COUNT(*) as cnt,
                       MAX(time_created) as latest
                FROM debug_logs
                WHERE jsonblob->>'litellm_call_id' IS NOT NULL
                GROUP BY cid
                ORDER BY latest DESC
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                cid = row["cid"]
                if not cid:
                    continue
                out.append(CallIdInfo(call_id=cid, count=int(row["cnt"]), latest=row["latest"]))
    except Exception as exc:
        logger.error("Error fetching recent call ids: %s", exc)
    return out


@router.get("/api/hooks/recent_traces", response_model=list[TraceInfo])
async def recent_traces(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[TraceInfo]:
    """Return recent trace ids with call/event counts."""
    out: list[TraceInfo] = []
    if config.database_url is None or pool is None:
        return out
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT trace_id,
                       COUNT(*) AS event_count,
                       COUNT(DISTINCT call_id) AS call_count,
                       MAX(time_created) AS latest
                FROM (
                    SELECT COALESCE(
                               jsonblob->>'litellm_trace_id',
                               jsonblob->'payload'->'request_data'->>'litellm_trace_id',
                               jsonblob->'payload'->'data'->>'litellm_trace_id'
                           ) AS trace_id,
                           jsonblob->>'litellm_call_id' AS call_id,
                           time_created
                    FROM debug_logs
                ) AS traces
                WHERE trace_id IS NOT NULL
                GROUP BY trace_id
                ORDER BY latest DESC
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                trace_id = row["trace_id"]
                if not trace_id:
                    continue
                out.append(
                    TraceInfo(
                        trace_id=trace_id,
                        call_count=int(row["call_count"]),
                        event_count=int(row["event_count"]),
                        latest=row["latest"],
                    )
                )
    except Exception as exc:
        logger.error("Error fetching recent traces: %s", exc)
    return out


@router.get("/api/hooks/conversation", response_model=ConversationSnapshot)
async def conversation_snapshot(
    call_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> ConversationSnapshot:
    """Return normalized conversation events for a call ID."""
    entries = await fetch_trace_entries(call_id, pool, config)
    events = events_from_trace_entries(entries)
    trace_id = next((evt.trace_id for evt in events if evt.trace_id), None)
    calls = build_call_snapshots(events)
    return ConversationSnapshot(call_id=call_id, trace_id=trace_id, events=events, calls=calls)


@router.get("/api/hooks/conversation/stream")
async def conversation_stream(
    call_id: str = Query(..., min_length=4),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
    _: None = Depends(enforce_conversation_rate_limit),
    stream_config: ConversationStreamConfig = Depends(get_conversation_stream_config),
) -> StreamingResponse:
    """Stream live conversation deltas for a call ID via SSE."""
    stream = conversation_sse_stream(redis_conn, call_id, config=stream_config)
    return StreamingResponse(stream, media_type="text/event-stream")


@router.get("/api/hooks/conversation/by_trace", response_model=TraceConversationSnapshot)
async def conversation_snapshot_by_trace(
    trace_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceConversationSnapshot:
    """Return normalized conversation events grouped by trace id."""
    entries = await fetch_trace_entries_by_trace(trace_id, pool, config)
    events = events_from_trace_entries(entries)
    call_ids = sorted({evt.call_id for evt in events if evt.call_id})
    calls = build_call_snapshots(events)
    return TraceConversationSnapshot(trace_id=trace_id, call_ids=call_ids, events=events, calls=calls)


@router.get("/api/hooks/conversation/stream_by_trace")
async def conversation_stream_by_trace(
    trace_id: str = Query(..., min_length=4),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
    _: None = Depends(enforce_conversation_rate_limit),
    stream_config: ConversationStreamConfig = Depends(get_conversation_stream_config),
) -> StreamingResponse:
    """Stream live conversation deltas for a trace id via SSE."""
    stream = conversation_sse_stream_by_trace(redis_conn, trace_id, config=stream_config)
    return StreamingResponse(stream, media_type="text/event-stream")


__all__ = [
    "router",
    "get_hook_counters",
    "hook_generic",
    "trace_by_call_id",
    "recent_call_ids",
    "recent_traces",
    "conversation_snapshot",
    "conversation_stream",
    "conversation_snapshot_by_trace",
    "conversation_stream_by_trace",
    "ConversationMessageDiff",
]
