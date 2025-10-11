"""HTTP routes for receiving LiteLLM hook callbacks and trace queries."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from copy import deepcopy
from typing import Annotated, Awaitable, Callable, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from luthien_proxy.control_plane.activity_stream import global_activity_sse_stream
from luthien_proxy.control_plane.conversation import (
    CallIdInfo,
    ConversationMessageDiff,
    ConversationSnapshot,
    build_call_snapshots,
    conversation_sse_stream,
    json_safe,
    load_events_for_call,
    load_recent_calls,
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
from .hook_result_handler import log_and_publish_hook_result, prepare_policy_payload
from .utils.rate_limiter import RateLimiter
from .utils.task_queue import DEBUG_LOG_QUEUE

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
    """Generic hook endpoint for any CustomLogger hook.

    DATAFLOW:
    1. Log original payload → DEBUG_LOG_QUEUE → debug_logs table
    2. Invoke policy.{hook_name}(**payload) → get transformed result
    3. Log/persist/publish result:
       - DEBUG_LOG_QUEUE → debug_logs table
       - CONVERSATION_EVENT_QUEUE → conversation_events table
       - CONVERSATION_EVENT_QUEUE → Redis pub/sub (luthien:conversation:{call_id})
    4. Return result to callback

    See: hook_result_handler.py for logging/publishing implementation
    """
    try:
        # === PAYLOAD PREPARATION ===
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

        # === POLICY INVOCATION ===
        handler = cast(
            Optional[Callable[..., Awaitable[JSONValue | None]]],
            getattr(policy, name, None),
        )

        handler_result: JSONValue | None = None
        if handler:
            policy_payload = cast(JSONObject, strip_post_time_ns(payload))
            filtered_payload = prepare_policy_payload(handler, policy_payload)
            handler_result = await handler(**filtered_payload)
        final_result: JSONValue = handler_result if handler_result is not None else payload

        # === RESULT LOGGING/PUBLISHING ===
        sanitized_result = cast(JSONObject, json_safe(final_result))
        call_id = record.get("litellm_call_id")

        log_and_publish_hook_result(
            hook_name=hook_name,
            call_id=call_id if isinstance(call_id, str) else None,
            trace_id=trace_id,
            original_payload=stored_payload,
            result_payload=sanitized_result,
            debug_writer=debug_writer,
            redis_conn=redis_conn,
            db_pool=pool,
        )

        result_to_return = strip_post_time_ns(final_result)
        logger.info(f"Hook {hook_name} returning: type={type(result_to_return)}, preview={str(result_to_return)[:200]}")
        return result_to_return
    except Exception as exc:
        import traceback

        logger.error(f"hook_generic_error in {hook_name}: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"hook_generic_error in {hook_name}: {exc}")


@router.get("/api/hooks/recent_call_ids", response_model=list[CallIdInfo])
async def recent_call_ids(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[CallIdInfo]:
    """Return recent call IDs observed in structured conversation tables."""
    return await load_recent_calls(limit, pool, config)


@router.get("/api/hooks/conversation", response_model=ConversationSnapshot)
async def conversation_snapshot(
    call_id: Annotated[str, Query(min_length=4)],
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> ConversationSnapshot:
    """Return normalized conversation events for a call ID."""
    events = await load_events_for_call(call_id, pool, config)
    calls = build_call_snapshots(events)
    return ConversationSnapshot(call_id=call_id, trace_id=None, events=events, calls=calls)


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


@router.get("/api/activity/stream")
async def global_activity_stream(
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
    _: None = Depends(enforce_conversation_rate_limit),
    stream_config: ConversationStreamConfig = Depends(get_conversation_stream_config),
) -> StreamingResponse:
    """Stream ALL control plane activity via SSE.

    This endpoint publishes events from all calls to a global channel,
    allowing you to see all proxy activity without knowing call IDs in advance.
    """
    stream = global_activity_sse_stream(redis_conn, config=stream_config)
    return StreamingResponse(stream, media_type="text/event-stream")


__all__ = [
    "router",
    "get_hook_counters",
    "hook_generic",
    "recent_call_ids",
    "conversation_snapshot",
    "conversation_stream",
    "global_activity_stream",
    "ConversationMessageDiff",
]
