"""ABOUTME: Global activity stream - publishes ALL control plane activity to Redis.

ABOUTME: Provides a unified view of all requests/responses across all calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

from pydantic import BaseModel, Field

from luthien_proxy.types import JSONObject, JSONValue
from luthien_proxy.utils import redis_client
from luthien_proxy.utils.project_config import ConversationStreamConfig

logger = logging.getLogger(__name__)

# Global activity channel - all events published here
_GLOBAL_ACTIVITY_CHANNEL = "luthien:activity:global"


class ActivityEvent(BaseModel):
    """A single activity event in the global stream.

    Each event represents one discrete moment in the request/response lifecycle:
    - original_request: The request as received from the client (pre-policy)
    - final_request: The request after policy transformation (post-policy)
    - original_response: The response from the LLM (pre-policy)
    - final_response: The response after policy transformation (post-policy)
    """

    timestamp: str = Field(description="ISO 8601 timestamp")
    event_type: str = Field(
        description="Type: original_request, final_request, original_response, final_response, error"
    )
    call_id: str = Field(description="LiteLLM call ID, 'unknown' if not available")
    trace_id: Optional[str] = Field(default=None, description="LiteLLM trace ID if available")
    hook: Optional[str] = Field(default=None, description="Hook name that generated this event")
    summary: str = Field(description="Human-readable summary of the event")
    payload: JSONValue = Field(default_factory=dict, description="Event-specific data")

    model_config = {"extra": "forbid"}


def global_activity_channel() -> str:
    """Redis pub/sub channel name for global activity stream."""
    return _GLOBAL_ACTIVITY_CHANNEL


async def publish_activity_event(
    redis: redis_client.RedisClient,
    event: ActivityEvent | JSONObject,
) -> None:
    """Publish an activity event to the global channel.

    Events are published to a single global channel so UIs can see ALL
    activity across ALL calls without knowing call IDs in advance.
    """
    try:
        if isinstance(event, ActivityEvent):
            payload = event.model_dump_json()
        else:
            try:
                payload = json.dumps(event, ensure_ascii=False)
            except TypeError as exc:
                logger.error("Failed to serialize activity event without fallback: %s", exc)
                try:
                    payload = json.dumps(event, ensure_ascii=False, default=str)
                except Exception as fallback_exc:  # pragma: no cover
                    logger.error("Fallback serialization for activity event failed: %s", fallback_exc)
                    return
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to serialize activity event: %s", exc)
        return

    try:
        await redis.publish(global_activity_channel(), payload)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to publish activity event: %s", exc)


async def _stream_from_global_channel(
    redis: redis_client.RedisClient,
    config: Optional[ConversationStreamConfig] = None,
) -> AsyncGenerator[str, None]:
    """Internal helper yielding SSE frames for the global activity channel."""
    heartbeat_interval = 15.0 if config is None else max(0.5, config.heartbeat_seconds)
    timeout = 1.0 if config is None else max(0.1, config.redis_poll_timeout_seconds)

    async with redis.pubsub() as pubsub:
        await pubsub.subscribe(global_activity_channel())
        last_heartbeat = time.monotonic()

        try:
            backoff = 0.1
            while True:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=timeout,
                    )
                    backoff = 0.1
                except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
                    raise
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.error(
                        "Global activity stream poll error: %s; retrying in %.1fs",
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    continue

                now = time.monotonic()
                if message is None:
                    if now - last_heartbeat >= heartbeat_interval:
                        last_heartbeat = now
                        yield ": ping\n\n"
                    continue

                data = message.get("data")
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="ignore")
                else:
                    text = str(data)
                last_heartbeat = now
                yield f"data: {text}\n\n"
        finally:
            try:
                await pubsub.unsubscribe(global_activity_channel())
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("Failed to unsubscribe from global activity channel", exc_info=True)


async def global_activity_sse_stream(
    redis: redis_client.RedisClient,
    config: Optional[ConversationStreamConfig] = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE data for the global activity channel."""
    async for chunk in _stream_from_global_channel(redis, config=config):
        yield chunk


def build_activity_events(
    hook: str,
    call_id: Optional[str],
    trace_id: Optional[str],
    original: Optional[JSONObject],
    result: Optional[JSONObject],
) -> list[ActivityEvent]:
    """Build activity events from hook data.

    Returns a list of events - one for each significant moment:
    - For pre_call_hook: original_request and final_request (if different)
    - For post_call_success_hook: original_response and final_response (if different)
    - For post_call_streaming_hook: original_response and final_response (if different)
    - For failure hooks: error event
    """
    events: list[ActivityEvent] = []
    timestamp = datetime.now(timezone.utc).isoformat()
    call_id_str = call_id or "unknown"

    if hook == "async_pre_call_hook":
        # Extract original (pre-policy) and final (post-policy) request data
        original_data = original.get("data") if isinstance(original, dict) else None
        if not isinstance(result, dict):
            raise ValueError("Pre-call hook result payload must be a dict with 'data'.")
        result_data = result.get("data")
        if not isinstance(result_data, dict):
            raise ValueError("Pre-call hook result payload must include a dict 'data' field.")

        # Always emit original_request
        if isinstance(original_data, dict):
            model = original_data.get("model", "unknown")
            messages = original_data.get("messages", [])
            msg_count = len(messages) if isinstance(messages, list) else 0

            events.append(
                ActivityEvent(
                    timestamp=timestamp,
                    event_type="original_request",
                    call_id=call_id_str,
                    trace_id=trace_id,
                    hook=hook,
                    summary=f"Original request to {model} ({msg_count} messages)",
                    payload={
                        "model": model,
                        "messages": messages,
                        "message_count": msg_count,
                        "has_tools": bool(original_data.get("tools")),
                        "tools": original_data.get("tools"),
                    },
                )
            )

        # ALWAYS emit final_request (even if unchanged)
        if isinstance(result_data, dict):
            model = result_data.get("model", "unknown")
            messages = result_data.get("messages", [])
            msg_count = len(messages) if isinstance(messages, list) else 0

            # Check if modified
            modified = result_data != original_data

            events.append(
                ActivityEvent(
                    timestamp=timestamp,
                    event_type="final_request",
                    call_id=call_id_str,
                    trace_id=trace_id,
                    hook=hook,
                    summary=f"Final request to {model} ({msg_count} messages)",
                    payload={
                        "model": model,
                        "messages": messages,
                        "message_count": msg_count,
                        "has_tools": bool(result_data.get("tools")),
                        "tools": result_data.get("tools"),
                        "modified": modified,
                    },
                )
            )

    elif hook == "async_post_call_success_hook":
        # Extract original (pre-policy) and final (post-policy) response
        original_response = original.get("response") if isinstance(original, dict) else None
        result_response = result.get("response") if isinstance(result, dict) else original_response

        # Always emit original_response
        if isinstance(original_response, dict):
            content = _extract_response_content(original_response)
            preview = content[:100] if content else ""

            events.append(
                ActivityEvent(
                    timestamp=timestamp,
                    event_type="original_response",
                    call_id=call_id_str,
                    trace_id=trace_id,
                    hook=hook,
                    summary=f"Original response: {preview}..."
                    if len(preview) == 100
                    else f"Original response: {preview}",
                    payload={
                        "content": content,
                        "content_length": len(content) if content else 0,
                        "has_tool_calls": _has_tool_calls(original_response),
                    },
                )
            )

        # ALWAYS emit final_response (even if unchanged)
        if isinstance(result_response, dict):
            content = _extract_response_content(result_response)
            preview = content[:100] if content else ""

            # Check if modified
            modified = result_response != original_response
            summary = f"Final response: {preview}..." if len(preview) == 100 else f"Final response: {preview}"

            events.append(
                ActivityEvent(
                    timestamp=timestamp,
                    event_type="final_response",
                    call_id=call_id_str,
                    trace_id=trace_id,
                    hook=hook,
                    summary=summary,
                    payload={
                        "content": content,
                        "content_length": len(content) if content else 0,
                        "has_tool_calls": _has_tool_calls(result_response),
                        "modified": modified,
                    },
                )
            )

    elif hook == "async_post_call_streaming_hook":
        # For streaming, we get accumulated responses
        original_response = original.get("response") if isinstance(original, dict) else None
        result_response = result.get("response") if isinstance(result, dict) else original_response

        # Always emit original_response
        if isinstance(original_response, dict):
            content = _extract_response_content(original_response)
            preview = content[:100] if content else ""

            events.append(
                ActivityEvent(
                    timestamp=timestamp,
                    event_type="original_response",
                    call_id=call_id_str,
                    trace_id=trace_id,
                    hook=hook,
                    summary=f"Original streaming response: {preview}..."
                    if len(preview) == 100
                    else f"Original streaming response: {preview}",
                    payload={
                        "content": content,
                        "content_length": len(content) if content else 0,
                        "streaming": True,
                    },
                )
            )

        # ALWAYS emit final_response (even if unchanged)
        if isinstance(result_response, dict):
            content = _extract_response_content(result_response)
            preview = content[:100] if content else ""

            # Check if modified
            modified = result_response != original_response
            summary = (
                f"Final streaming response: {preview}..."
                if len(preview) == 100
                else f"Final streaming response: {preview}"
            )

            events.append(
                ActivityEvent(
                    timestamp=timestamp,
                    event_type="final_response",
                    call_id=call_id_str,
                    trace_id=trace_id,
                    hook=hook,
                    summary=summary,
                    payload={
                        "content": content,
                        "content_length": len(content) if content else 0,
                        "streaming": True,
                        "modified": modified,
                    },
                )
            )

    elif hook == "async_post_call_failure_hook":
        events.append(
            ActivityEvent(
                timestamp=timestamp,
                event_type="error",
                call_id=call_id_str,
                trace_id=trace_id,
                hook=hook,
                summary="Request failed",
                payload={"error": str(result) if result else "Unknown error"},
            )
        )

    return events


def _extract_response_content(response: dict[str, Any]) -> str:
    """Extract text content from a response object."""
    choices = response.get("choices", [])
    if choices and isinstance(choices, list):
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message", {})
            if isinstance(message, dict):
                return message.get("content", "")
    return ""


def _has_tool_calls(response: dict[str, Any]) -> bool:
    """Check if response contains tool calls."""
    choices = response.get("choices", [])
    if choices and isinstance(choices, list):
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message", {})
            if isinstance(message, dict):
                return bool(message.get("tool_calls"))
    return False


__all__ = [
    "ActivityEvent",
    "publish_activity_event",
    "global_activity_sse_stream",
    "global_activity_channel",
    "build_activity_events",
]
