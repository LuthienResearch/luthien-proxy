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
        # Extract request data - both original (pre-policy) and result (post-policy)
        original_payload = require_dict(original, "pre-call original payload")
        result_payload = require_dict(result, "pre-call result payload") if result is not None else original_payload

        # Extract from original (pre-policy)
        original_data = original_payload.get("data")
        if not isinstance(original_data, dict):
            return events

        original_request = {
            "messages": original_data.get("messages", []),
            "model": original_data.get("model"),
            "temperature": original_data.get("temperature"),
            "max_tokens": original_data.get("max_tokens"),
            "tools": original_data.get("tools"),
            "tool_choice": original_data.get("tool_choice"),
        }
        # Remove None values
        original_request = {k: v for k, v in original_request.items() if v is not None}

        # Extract from result (post-policy)
        result_data = result_payload.get("data")
        if not isinstance(result_data, dict):
            return events

        final_request = {
            "messages": result_data.get("messages", []),
            "model": result_data.get("model"),
            "temperature": result_data.get("temperature"),
            "max_tokens": result_data.get("max_tokens"),
            "tools": result_data.get("tools"),
            "tool_choice": result_data.get("tool_choice"),
        }
        # Remove None values
        final_request = {k: v for k, v in final_request.items() if v is not None}

        # Store both original and final in payload
        request_event_payload = {
            "original": original_request,
            "final": final_request,
            # For backwards compatibility, top-level fields use final (post-policy) values
            "messages": final_request.get("messages", []),
            "model": final_request.get("model"),
            "temperature": final_request.get("temperature"),
            "max_tokens": final_request.get("max_tokens"),
            "tools": final_request.get("tools"),
            "tool_choice": final_request.get("tool_choice"),
        }

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
        # Extract request data from the 'data' field (may have been missing from pre-call)
        # This ensures we capture the request even when pre-call hook didn't have a call_id
        original_data = original.get("data") if isinstance(original, dict) else None
        if isinstance(original_data, dict):
            result_data = result.get("data") if isinstance(result, dict) else original_data
            if not isinstance(result_data, dict):
                result_data = original_data

            # Extract original and final request
            original_request = {
                "messages": original_data.get("messages", []),
                "model": original_data.get("model"),
                "temperature": original_data.get("temperature"),
                "max_tokens": original_data.get("max_tokens"),
                "tools": original_data.get("tools"),
                "tool_choice": original_data.get("tool_choice"),
            }
            original_request = {k: v for k, v in original_request.items() if v is not None}

            final_request = {
                "messages": result_data.get("messages", []),
                "model": result_data.get("model"),
                "temperature": result_data.get("temperature"),
                "max_tokens": result_data.get("max_tokens"),
                "tools": result_data.get("tools"),
                "tool_choice": result_data.get("tool_choice"),
            }
            final_request = {k: v for k, v in final_request.items() if v is not None}

            # Create request event with both original and final
            request_event_payload = {
                "original": original_request,
                "final": final_request,
                # For backwards compatibility, top-level fields use final values
                "messages": final_request.get("messages", []),
                "model": final_request.get("model"),
                "temperature": final_request.get("temperature"),
                "max_tokens": final_request.get("max_tokens"),
                "tools": final_request.get("tools"),
                "tool_choice": final_request.get("tool_choice"),
            }

            events.append(
                ConversationEvent(
                    call_id=call_id,
                    trace_id=trace_id,
                    event_type="request",
                    sequence=sequence_ns - 1,  # Request comes before response
                    timestamp=timestamp,
                    hook=hook,
                    payload=request_event_payload,  # type: ignore[arg-type]
                )
            )

        # Extract response from original (pre-policy)
        original_response = original.get("response") if isinstance(original, dict) else None
        if not isinstance(original_response, dict):
            return events

        original_choices = original_response.get("choices", [])
        if not original_choices:
            return events

        original_first_choice = original_choices[0] if isinstance(original_choices, list) else {}
        if not isinstance(original_first_choice, dict):
            return events

        original_message = original_first_choice.get("message", {})
        original_finish_reason = original_first_choice.get("finish_reason")

        # Extract response from result (post-policy) - may be same as original
        result_response = result.get("response") if isinstance(result, dict) else None
        if not isinstance(result_response, dict):
            result_response = original_response

        result_choices = result_response.get("choices", [])
        result_first_choice = result_choices[0] if result_choices and isinstance(result_choices, list) else {}
        if not isinstance(result_first_choice, dict):
            result_first_choice = original_first_choice

        final_message = result_first_choice.get("message", {})
        final_finish_reason = result_first_choice.get("finish_reason")

        # Store both original and final in payload
        response_event_payload = {
            "original": {
                "message": original_message,
                "finish_reason": original_finish_reason,
            },
            "final": {
                "message": final_message,
                "finish_reason": final_finish_reason,
            },
            # For backwards compatibility, top-level fields use final (post-policy) values
            "message": final_message,
            "finish_reason": final_finish_reason,
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
        # Extract from original (pre-policy streamed response)
        original_payload = original if isinstance(original, dict) else {}
        original_response = original_payload.get("response", {})
        if not isinstance(original_response, dict):
            return events

        original_choices = original_response.get("choices", [])
        if not original_choices:
            return events

        original_first_choice = original_choices[0] if isinstance(original_choices, list) else {}
        if not isinstance(original_first_choice, dict):
            return events

        original_message = original_first_choice.get("message", {})
        original_finish_reason = original_first_choice.get("finish_reason")

        # Extract from result (post-policy streamed response) - may be same as original
        result_payload = result if result is not None else original
        if not isinstance(result_payload, dict):
            result_payload = original_payload

        result_response = result_payload.get("response", {})
        if not isinstance(result_response, dict):
            result_response = original_response

        result_choices = result_response.get("choices", [])
        result_first_choice = result_choices[0] if result_choices and isinstance(result_choices, list) else {}
        if not isinstance(result_first_choice, dict):
            result_first_choice = original_first_choice

        final_message = result_first_choice.get("message", {})
        final_finish_reason = result_first_choice.get("finish_reason")

        # Store both original and final in payload
        response_event_payload = {
            "original": {
                "message": original_message,
                "finish_reason": original_finish_reason,
            },
            "final": {
                "message": final_message,
                "finish_reason": final_finish_reason,
            },
            # For backwards compatibility, top-level fields use final (post-policy) values
            "message": final_message,
            "finish_reason": final_finish_reason,
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
