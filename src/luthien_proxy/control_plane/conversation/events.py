"""Conversation event builders for request/response pairs."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from luthien_proxy.types import JSONValue

from .models import ConversationEvent
from .utils import derive_sequence_ns, require_dict


def build_conversation_events(
    hook: str,
    call_id: Optional[str],
    trace_id: Optional[str],
    original: JSONValue | None,
    result: JSONValue | None,
    timestamp_ns_fallback: int,
    timestamp: datetime,
) -> list[ConversationEvent]:
    """Translate a hook invocation into request/response events."""
    if not isinstance(call_id, str) or not call_id:
        return []

    sequence_ns = derive_sequence_ns(timestamp_ns_fallback, original, result)
    events: list[ConversationEvent] = []

    if hook == "async_pre_call_hook":
        # Extract request data
        original_payload = require_dict(original, "pre-call original payload")
        result_payload = require_dict(result, "pre-call result payload") if result is not None else original_payload

        # Use the result payload (post-policy) as the canonical request
        data = result_payload.get("data")
        if not isinstance(data, dict):
            return events

        request_event_payload = {
            "messages": data.get("messages", []),
            "model": data.get("model"),
            "temperature": data.get("temperature"),
            "max_tokens": data.get("max_tokens"),
            "tools": data.get("tools"),
            "tool_choice": data.get("tool_choice"),
        }
        # Remove None values
        request_event_payload = {k: v for k, v in request_event_payload.items() if v is not None}

        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=None,  # trace_id no longer used
                event_type="request",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload=request_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    if hook == "async_post_call_success_hook":
        # Extract response from the original (pre-policy) response
        original_response = original.get("response") if isinstance(original, dict) else None
        if not isinstance(original_response, dict):
            return events

        choices = original_response.get("choices", [])
        if not choices:
            return events

        first_choice = choices[0] if isinstance(choices, list) else {}
        if not isinstance(first_choice, dict):
            return events

        message = first_choice.get("message", {})
        finish_reason = first_choice.get("finish_reason")

        response_event_payload = {
            "message": message,
            "finish_reason": finish_reason,
            "status": "success",
        }

        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=None,
                event_type="response",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload=response_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    if hook == "async_post_call_streaming_hook":
        # Extract final streamed response
        summary_payload = result if result is not None else original
        if not isinstance(summary_payload, dict):
            return events

        response = summary_payload.get("response", {})
        if not isinstance(response, dict):
            return events

        choices = response.get("choices", [])
        if not choices:
            return events

        first_choice = choices[0] if isinstance(choices, list) else {}
        if not isinstance(first_choice, dict):
            return events

        message = first_choice.get("message", {})
        finish_reason = first_choice.get("finish_reason")

        response_event_payload = {
            "message": message,
            "finish_reason": finish_reason,
            "status": "success",
        }

        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=None,
                event_type="response",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload=response_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    if hook == "async_post_call_failure_hook":
        response_event_payload = {
            "message": {},
            "finish_reason": None,
            "status": "failure",
        }

        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=None,
                event_type="response",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload=response_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    # Ignore streaming chunk hooks - we only store final request/response
    return events


# Stub functions for backwards compatibility (no-op)
def reset_stream_indices(call_id: str) -> None:
    """Stub - streaming chunk indices no longer tracked."""
    pass


def clear_stream_indices(call_id: str) -> None:
    """Stub - streaming chunk indices no longer tracked."""
    pass


def next_chunk_index(call_id: str, stream: str) -> int:
    """Stub - streaming chunk indices no longer tracked."""
    return 0


__all__ = [
    "build_conversation_events",
    "reset_stream_indices",
    "clear_stream_indices",
    "next_chunk_index",
]
