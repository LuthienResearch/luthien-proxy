"""OpenAI Responses stream folding."""

from __future__ import annotations

from dataclasses import dataclass

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    ensure_not_truncated,
    fail,
    is_json_object,
    is_json_sequence,
    optional_string,
)
from luthien_proxy.passthrough_materialize.payloads import JsonObject


@dataclass(frozen=True, slots=True)
class ResponseStreamFold:
    """Folded Responses stream state before canonical conversion."""

    text: str
    completed: JsonObject | None


LIFECYCLE_EVENTS = frozenset(
    {
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
    }
)


def fold_response_stream(
    endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None
) -> ResponseStreamFold:
    """Fold captured OpenAI Responses SSE chunks into one response."""
    ensure_not_truncated(endpoint, response, transaction_id)
    events = response.get("events")
    if not is_json_sequence(events):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "events")
    text = ""
    completed: JsonObject | None = None
    for event in events:
        if not is_json_object(event):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "stream event")
        event_type = optional_string(event, "type")
        match event_type:
            case "response.output_text.delta":
                text += optional_string(event, "delta") or ""
            case "response.completed":
                response_obj = event.get("response")
                if is_json_object(response_obj):
                    completed = response_obj
            case str() if event_type in LIFECYCLE_EVENTS:
                continue
            case str():
                fail(
                    endpoint,
                    transaction_id,
                    PassthroughNormalizeReason.UNSUPPORTED_VARIANT,
                    f"stream event.type:{event_type}",
                )
            case _:
                fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "stream event.type")
    return ResponseStreamFold(text=text, completed=completed)
