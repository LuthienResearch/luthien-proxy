"""ABOUTME: Helper functions for logging, persisting, and publishing hook results.

ABOUTME: Centralizes the standard post-policy workflow for non-streaming hooks.
"""

from __future__ import annotations

import inspect
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from luthien_proxy.control_plane.activity_stream import build_activity_events, publish_activity_event
from luthien_proxy.control_plane.conversation import (
    build_conversation_events,
    publish_conversation_event,
    record_conversation_events,
)
from luthien_proxy.control_plane.dependencies import DebugLogWriter
from luthien_proxy.control_plane.utils.task_queue import (
    CONVERSATION_EVENT_QUEUE,
    DEBUG_LOG_QUEUE,
)
from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db, redis_client


def log_and_publish_hook_result(
    *,
    hook_name: str,
    call_id: Optional[str],
    trace_id: Optional[str],
    original_payload: JSONObject,
    result_payload: JSONObject,
    debug_writer: DebugLogWriter,
    redis_conn: redis_client.RedisClient,
    db_pool: Optional[db.DatabasePool],
) -> None:
    """Log hook result to debug_logs, record conversation events, publish to Redis.

    This encapsulates the standard post-policy workflow:
    1. Write result to debug_logs via debug_writer
    2. Build and record conversation_events (if call_id available)
    3. Publish events to Redis pub/sub (per-call and global channels)

    All operations run in background via task queues and are best-effort.

    Note: Synchronous function that submits async work to queues.
    Timestamps captured internally for consistency.
    """
    # Capture timestamps once for consistency
    timestamp_ns = time.time_ns()
    timestamp = datetime.now(timezone.utc)

    # Log result to debug_logs
    result_record: JSONObject = {
        "hook": hook_name,
        "litellm_call_id": call_id,
        "original": original_payload,
        "result": result_payload,
        "post_time_ns": timestamp_ns,
    }
    if trace_id:
        result_record["litellm_trace_id"] = trace_id

    DEBUG_LOG_QUEUE.submit(debug_writer(f"hook_result:{hook_name}", result_record))

    # Publish to global activity stream (all hooks) - may publish multiple events
    activity_events = build_activity_events(
        hook=hook_name,
        call_id=call_id,
        trace_id=trace_id,
        original=original_payload,
        result=result_payload,
    )
    for activity_event in activity_events:
        CONVERSATION_EVENT_QUEUE.submit(publish_activity_event(redis_conn, activity_event))

    # Build and persist conversation events (if we have a call_id)
    if isinstance(call_id, str) and call_id:
        events = build_conversation_events(
            hook=hook_name,
            call_id=call_id,
            trace_id=trace_id,
            original=original_payload,
            result=result_payload,
            timestamp_ns_fallback=timestamp_ns,
            timestamp=timestamp,
        )

        if events:
            # Submit to database
            CONVERSATION_EVENT_QUEUE.submit(record_conversation_events(db_pool, events))

            # Publish to Redis per-call channels
            for event in events:
                CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(redis_conn, event))


def prepare_policy_payload(handler: Callable, payload: JSONObject) -> JSONObject:
    """Filter payload to match policy handler's signature.

    Policies can accept **kwargs (get full payload) or specific named parameters.
    This inspects the handler signature and returns only matching keys.
    """
    signature = inspect.signature(handler)
    parameters = signature.parameters

    # If handler accepts **kwargs, pass everything
    accepts_var_kw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
    if accepts_var_kw:
        return payload

    # Otherwise, filter to named parameters
    parameter_names = {name for name in parameters.keys() if name != "self"}
    return {k: v for k, v in payload.items() if k in parameter_names}
