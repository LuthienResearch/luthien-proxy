"""OpenAI Chat Completions stream folding."""

from __future__ import annotations

from dataclasses import dataclass

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    canonical_usage,
    ensure_not_truncated,
    fail,
    is_json_object,
    is_json_sequence,
    json_object_from_string,
    optional_string,
    sequence_field,
    stop_reason,
)
from luthien_proxy.passthrough_materialize.payloads import JsonMutableObject, JsonMutableValue, JsonObject, JsonValue


@dataclass(slots=True)
class StreamToolCall:
    """Mutable accumulator for indexed Chat Completions tool-call deltas."""

    call_id: str | None = None
    name: str | None = None
    arguments: str = ""


def stream_chat_response(
    endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None
) -> JsonMutableObject:
    """Fold captured OpenAI SSE Chat Completions chunks into one response."""
    ensure_not_truncated(endpoint, response, transaction_id)
    events = sequence_field(endpoint, response, "events", transaction_id)
    content = ""
    refusal = ""
    model: str | None = None
    response_id: str | None = None
    finish_reason: str | None = None
    usage: JsonMutableObject | None = None
    tool_calls: dict[int, StreamToolCall] = {}
    for event in events:
        if not is_json_object(event):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "stream event")
        model = optional_string(event, "model") or model
        response_id = optional_string(event, "id") or response_id
        event_usage = canonical_usage(event.get("usage"))
        usage = event_usage or usage
        choices = event.get("choices")
        if not is_json_sequence(choices) or not choices:
            continue
        first = choices[0]
        if not is_json_object(first):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "stream choice")
        finish_reason = optional_string(first, "finish_reason") or finish_reason
        delta = first.get("delta")
        if is_json_object(delta):
            content += optional_string(delta, "content") or ""
            refusal += optional_string(delta, "refusal") or ""
            _accumulate_tool_calls(endpoint, delta.get("tool_calls"), tool_calls, transaction_id)
    final = _stream_final_content(endpoint, content, refusal, tool_calls, transaction_id)
    result: JsonMutableObject = {"role": "assistant", "content": final, "stop_reason": stop_reason(finish_reason)}
    if response_id is not None:
        result["id"] = response_id
    if model is not None:
        result["model"] = model
    if usage is not None:
        result["usage"] = usage
    return result


def _accumulate_tool_calls(
    endpoint: EligibleEndpoint,
    raw_calls: JsonValue,
    tool_calls: dict[int, StreamToolCall],
    transaction_id: str | None,
) -> None:
    if raw_calls is None:
        return
    if not is_json_sequence(raw_calls):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "delta.tool_calls")
    for raw_call in raw_calls:
        if not is_json_object(raw_call):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "delta.tool_call")
        index = raw_call.get("index")
        if not isinstance(index, int):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "delta.tool_call.index")
        call = tool_calls.setdefault(index, StreamToolCall())
        call.call_id = optional_string(raw_call, "id") or call.call_id
        function = raw_call.get("function")
        if is_json_object(function):
            call.name = optional_string(function, "name") or call.name
            call.arguments += optional_string(function, "arguments") or ""


def _stream_final_content(
    endpoint: EligibleEndpoint,
    text: str,
    refusal: str,
    tool_calls: dict[int, StreamToolCall],
    transaction_id: str | None,
) -> list[JsonMutableValue]:
    content: list[JsonMutableValue] = []
    if text:
        content.append({"type": "text", "text": text})
    if refusal:
        content.append({"type": "text", "text": refusal})
    for index in sorted(tool_calls):
        call = tool_calls[index]
        if not call.call_id:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "tool_call.id")
        if call.name is None:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "tool_call.function.name")
        content.append(
            {
                "type": "tool_use",
                "id": call.call_id,
                "name": call.name,
                "input": json_object_from_string(endpoint, call.arguments or "{}", transaction_id),
            }
        )
    return content
