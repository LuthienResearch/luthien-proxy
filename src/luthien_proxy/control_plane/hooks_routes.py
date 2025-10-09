"""HTTP routes for receiving LiteLLM hook callbacks and trace queries."""

from __future__ import annotations

import inspect
import json
import logging
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Annotated, Awaitable, Callable, Optional, cast

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
    fetch_trace_entries,
    json_safe,
    load_events_for_call,
    load_events_for_trace,
    load_recent_calls,
    load_recent_traces,
    publish_conversation_event,
    publish_trace_conversation_event,
    record_conversation_events,
    strip_post_time_ns,
)
from luthien_proxy.control_plane.conversation.utils import extract_trace_id
from luthien_proxy.control_plane.utils.hooks import extract_call_id_for_hook
from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.types import JSONObject, JSONValue
from luthien_proxy.utils import db, redis_client
from luthien_proxy.utils.project_config import ConversationStreamConfig, ProjectConfig

from .dependencies import (
    DebugLogWriter,
    get_active_policy,
    get_conversation_rate_limiter,
    get_conversation_stream_config,
    get_database_pool,
    get_debug_log_writer,
    get_hook_counter_state,
    get_project_config,
    get_redis_client,
)
from .utils.rate_limiter import RateLimiter
from .utils.task_queue import CONVERSATION_EVENT_QUEUE, DEBUG_LOG_QUEUE

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
    payload: JSONObject,
    debug_writer: DebugLogWriter = Depends(get_debug_log_writer),
    policy: LuthienPolicy = Depends(get_active_policy),
    counters: Counter[str] = Depends(get_hook_counter_state),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
    pool: db.DatabasePool | None = Depends(get_database_pool),
) -> JSONValue:
    """Generic hook endpoint for any CustomLogger hook."""
    try:
        record_payload = cast(JSONObject, json_safe(payload))
        stored_payload: JSONObject = deepcopy(record_payload)
        record: JSONObject = {
            "hook": hook_name,
            "payload": record_payload,
        }
        logger.debug(f"hook={hook_name} payload={json.dumps(record_payload, ensure_ascii=False)}")
        try:
            call_id = extract_call_id_for_hook(hook_name, payload)
            if isinstance(call_id, str) and call_id:
                record["litellm_call_id"] = call_id
        except Exception:
            pass

        stored_record: JSONObject = {"hook": hook_name, "payload": stored_payload}
        stored_record["post_time_ns"] = time.time_ns()
        trace_id = extract_trace_id(payload)
        if trace_id:
            record["litellm_trace_id"] = trace_id
            stored_record["litellm_trace_id"] = trace_id
        if "litellm_call_id" in record:
            stored_record["litellm_call_id"] = record["litellm_call_id"]
        DEBUG_LOG_QUEUE.submit(debug_writer(f"hook:{hook_name}", stored_record))
        name = hook_name.lower()
        counters[name] += 1
        handler = cast(
            Optional[Callable[..., Awaitable[JSONValue | None]]],
            getattr(policy, name, None),
        )

        handler_result: JSONValue | None = None
        if handler:
            policy_payload = cast(JSONObject, strip_post_time_ns(payload))
            signature = inspect.signature(handler)
            parameters = signature.parameters
            accepts_var_kw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
            if accepts_var_kw:
                filtered_payload = policy_payload
            else:
                parameter_names = {name for name in parameters.keys() if name != "self"}
                filtered_payload = {k: v for k, v in policy_payload.items() if k in parameter_names}
            handler_result = await handler(**filtered_payload)
        final_result: JSONValue = handler_result if handler_result is not None else payload

        sanitized_result = json_safe(final_result)

        result_record: JSONObject = {
            "hook": hook_name,
            "litellm_call_id": record.get("litellm_call_id"),
            "original": stored_payload,
            "result": sanitized_result,
        }
        result_record["post_time_ns"] = time.time_ns()
        if trace_id:
            result_record["litellm_trace_id"] = trace_id
        DEBUG_LOG_QUEUE.submit(debug_writer(f"hook_result:{hook_name}", result_record))

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
            if events:
                CONVERSATION_EVENT_QUEUE.submit(record_conversation_events(pool, events))
            for event in events:
                CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(redis_conn, event))
                CONVERSATION_EVENT_QUEUE.submit(publish_trace_conversation_event(redis_conn, event))

        result_to_return = strip_post_time_ns(final_result)
        logger.info(f"Hook {hook_name} returning: type={type(result_to_return)}, preview={str(result_to_return)[:200]}")
        return result_to_return
    except Exception as exc:
        import traceback

        logger.error(f"hook_generic_error in {hook_name}: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"hook_generic_error in {hook_name}: {exc}")


@router.get("/api/hooks/trace_by_call_id", response_model=TraceResponse)
async def trace_by_call_id(
    call_id: Annotated[str, Query(min_length=4)],
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceResponse:
    """Return ordered hook entries from debug_logs for a litellm_call_id."""
    entries, has_more = await fetch_trace_entries(call_id, pool, config, limit=limit, offset=offset)
    next_offset = offset + len(entries) if has_more else None
    return TraceResponse(
        call_id=call_id,
        entries=entries,
        offset=offset,
        limit=limit,
        has_more=has_more,
        next_offset=next_offset,
    )


@router.get("/api/hooks/recent_call_ids", response_model=list[CallIdInfo])
async def recent_call_ids(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[CallIdInfo]:
    """Return recent call IDs observed in structured conversation tables."""
    return await load_recent_calls(limit, pool, config)


@router.get("/api/hooks/recent_traces", response_model=list[TraceInfo])
async def recent_traces(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[TraceInfo]:
    """Return recent trace ids with call/event counts."""
    return await load_recent_traces(limit, pool, config)


@router.get("/api/hooks/conversation", response_model=ConversationSnapshot)
async def conversation_snapshot(
    call_id: Annotated[str, Query(min_length=4)],
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> ConversationSnapshot:
    """Return normalized conversation events for a call ID."""
    events = await load_events_for_call(call_id, pool, config)
    trace_id = next((evt.trace_id for evt in events if evt.trace_id), None)
    calls = build_call_snapshots(events)
    return ConversationSnapshot(call_id=call_id, trace_id=trace_id, events=events, calls=calls)


@router.get("/api/hooks/conversation/stream")
async def conversation_stream(
    call_id: Annotated[str, Query(min_length=4)],
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
    _: None = Depends(enforce_conversation_rate_limit),
    stream_config: ConversationStreamConfig = Depends(get_conversation_stream_config),
) -> StreamingResponse:
    """Stream live conversation deltas for a call ID via SSE."""
    stream = conversation_sse_stream(redis_conn, call_id, config=stream_config)
    return StreamingResponse(stream, media_type="text/event-stream")


@router.get("/api/hooks/conversation/by_trace", response_model=TraceConversationSnapshot)
async def conversation_snapshot_by_trace(
    trace_id: Annotated[str, Query(min_length=4)],
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceConversationSnapshot:
    """Return normalized conversation events grouped by trace id."""
    events = await load_events_for_trace(trace_id, pool, config)
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
