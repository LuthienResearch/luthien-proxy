"""Gemini stored-stream wrapper normalization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint
from luthien_proxy.passthrough_materialize.gemini_common import (
    gemini_content_free_parts,
    gemini_function_call_id,
    gemini_response_identifiers,
    gemini_stop_reason,
    gemini_usage,
)
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    ensure_not_truncated,
    fail,
    is_json_object,
    is_json_sequence,
    json_mutable,
    json_mutable_object,
    optional_string,
    sequence_field,
)
from luthien_proxy.passthrough_materialize.payloads import JsonMutableObject, JsonMutableValue, JsonObject, JsonValue
from luthien_proxy.passthrough_materialize.provider_models import parse_gemini_response


@dataclass(slots=True)
class _FunctionCallAccumulator:
    """Accumulate one identified streamed function call before immutable boundary construction."""

    name: str
    arguments: JsonMutableObject


@dataclass(slots=True)
class _StreamAccumulator:
    """Accumulate ordered provider chunks during one pure stream fold."""

    text: list[str] = field(default_factory=list)
    calls: dict[str, _FunctionCallAccumulator] = field(default_factory=dict)
    call_order: list[str] = field(default_factory=list)
    response_id: str | None = None
    model_version: str | None = None
    finish_reason: str | None = None
    usage: JsonMutableObject | None = None
    safety_ratings: JsonMutableValue | None = None
    prompt_feedback: JsonMutableObject | None = None


def normalize_gemini_stream_response(
    endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None
) -> JsonMutableObject:
    """Fold an exact Gemini JSON-array or SSE capture wrapper into one canonical response."""
    accumulator = _StreamAccumulator()
    for chunk in _stream_chunks(endpoint, response, transaction_id):
        _add_chunk(endpoint, accumulator, chunk, transaction_id)
    return _folded_response(endpoint, accumulator, transaction_id)


def _stream_chunks(endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None) -> Sequence[JsonValue]:
    ensure_not_truncated(endpoint, response, transaction_id)
    stream_format = optional_string(response, "stream_format")
    match stream_format:
        case "gemini-json-array" | "gemini-sse":
            pass
        case str():
            fail(
                endpoint,
                transaction_id,
                PassthroughNormalizeReason.UNSUPPORTED_VARIANT,
                f"stream_format:{stream_format}",
            )
        case None:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "stream_format")
    if "final" not in response:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "final")
    if response.get("final") is not None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "final")
    return sequence_field(endpoint, response, "chunks", transaction_id)


def _add_chunk(
    endpoint: EligibleEndpoint, accumulator: _StreamAccumulator, raw_chunk: JsonValue, transaction_id: str | None
) -> None:
    if not is_json_object(raw_chunk):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "stream chunk")
    try:
        raw_chunk = parse_gemini_response(raw_chunk).model_dump(mode="json", by_alias=True, exclude_none=True)
    except ValidationError:
        # Best-effort SDK: the google-genai response model is strict (extra='forbid')
        # and lags the live REST API (e.g. it rejects usageMetadata.serviceTier);
        # keep the raw chunk so the downstream lenient mapping can proceed. Mirrors
        # the buffered-response fallback in gemini_response.normalize_gemini_response.
        pass
    recognized = False
    response_id = raw_chunk.get("responseId")
    if response_id is not None:
        if not isinstance(response_id, str):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "responseId")
        accumulator.response_id = response_id
        recognized = True
    model_version = raw_chunk.get("modelVersion")
    if model_version is not None:
        if not isinstance(model_version, str):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "modelVersion")
        accumulator.model_version = model_version
        recognized = True
    usage = raw_chunk.get("usageMetadata")
    if usage is not None:
        accumulator.usage = gemini_usage(endpoint, usage, transaction_id)
        recognized = True
    feedback = raw_chunk.get("promptFeedback")
    if feedback is not None:
        if not is_json_object(feedback):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "promptFeedback")
        accumulator.prompt_feedback = json_mutable_object(feedback)
        recognized = True
    candidates = raw_chunk.get("candidates")
    if candidates is not None:
        if not is_json_sequence(candidates):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidates")
        for candidate in candidates:
            _add_candidate(endpoint, accumulator, candidate, transaction_id)
        recognized = True
    if not recognized:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_VARIANT, "stream chunk")


def _add_candidate(
    endpoint: EligibleEndpoint, accumulator: _StreamAccumulator, raw_candidate: JsonValue, transaction_id: str | None
) -> None:
    if not is_json_object(raw_candidate):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate")
    index = raw_candidate.get("index", 0)
    if not isinstance(index, int) or isinstance(index, bool):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.index")
    if index != 0:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_VARIANT, f"candidate.index:{index}")
    content = raw_candidate.get("content")
    if content is not None:
        if not is_json_object(content):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.content")
        _add_parts(endpoint, accumulator, content, transaction_id)
    finish_reason = raw_candidate.get("finishReason")
    if finish_reason is not None:
        if not isinstance(finish_reason, str):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.finishReason")
        accumulator.finish_reason = finish_reason
    safety_ratings = raw_candidate.get("safetyRatings")
    if safety_ratings is not None:
        if not is_json_sequence(safety_ratings):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.safetyRatings")
        accumulator.safety_ratings = json_mutable(safety_ratings)


def _add_parts(
    endpoint: EligibleEndpoint, accumulator: _StreamAccumulator, content: JsonObject, transaction_id: str | None
) -> None:
    role = optional_string(content, "role")
    match role:
        case "model":
            pass
        case None:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "candidate.content.role")
        case _:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_VARIANT, "candidate.content.role")
    function_ordinal = 0
    for raw_part in sequence_field(endpoint, content, "parts", transaction_id):
        if not is_json_object(raw_part):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.part")
        match raw_part:
            case {"text": str() as text}:
                accumulator.text.append(text)
            case {"functionCall": Mapping() as call}:
                _add_function_call(endpoint, accumulator, call, function_ordinal, transaction_id)
                function_ordinal += 1
            case {"functionResponse": _}:
                continue
            case _:
                continue


def _add_function_call(
    endpoint: EligibleEndpoint,
    accumulator: _StreamAccumulator,
    call: Mapping[str, JsonValue],
    ordinal: int,
    transaction_id: str | None,
) -> None:
    name = optional_string(call, "name")
    args = call.get("args")
    if name is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "functionCall.name")
    call_id = gemini_function_call_id(call, name, ordinal)
    if args is None:
        arguments: JsonMutableObject = {}
    elif is_json_object(args):
        arguments = json_mutable_object(args)
    else:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "functionCall.args")
    existing = accumulator.calls.get(call_id)
    if existing is None:
        accumulator.calls[call_id] = _FunctionCallAccumulator(name=name, arguments=arguments)
        accumulator.call_order.append(call_id)
        return
    if existing.name != name:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "functionCall.name conflict")
    for key, value in arguments.items():
        prior = existing.arguments.get(key)
        if prior is not None and prior != value:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "functionCall.args conflict")
        existing.arguments[key] = value


def _folded_response(
    endpoint: EligibleEndpoint, accumulator: _StreamAccumulator, transaction_id: str | None
) -> JsonMutableObject:
    if accumulator.prompt_feedback is not None:
        return _blocked_response(endpoint, accumulator, transaction_id)
    if accumulator.finish_reason is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "candidate.finishReason")
    content: list[JsonMutableValue] = []
    text = "".join(accumulator.text)
    if text:
        content.append({"type": "text", "text": text})
    for call_id in accumulator.call_order:
        call = accumulator.calls[call_id]
        content.append({"type": "tool_use", "id": call_id, "name": call.name, "input": call.arguments})
    stop_reason = gemini_stop_reason(endpoint, accumulator.finish_reason, transaction_id)
    if not content:
        content = gemini_content_free_parts(endpoint, stop_reason, transaction_id)
    final: JsonMutableObject = {
        "role": "assistant",
        "content": content,
        "stop_reason": stop_reason,
    }
    _add_metadata(final, accumulator)
    if accumulator.safety_ratings is not None:
        final["safety_ratings"] = accumulator.safety_ratings
    return final


def _blocked_response(
    endpoint: EligibleEndpoint, accumulator: _StreamAccumulator, transaction_id: str | None
) -> JsonMutableObject:
    feedback = accumulator.prompt_feedback
    if feedback is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "promptFeedback")
    if optional_string(feedback, "blockReason") is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "promptFeedback.blockReason")
    final: JsonMutableObject = {"role": "assistant", "content": [], "stop_reason": "blocked"}
    _add_metadata(final, accumulator)
    final["prompt_feedback"] = feedback
    return final


def _add_metadata(final: JsonMutableObject, accumulator: _StreamAccumulator) -> None:
    gemini_response_identifiers(
        {
            "responseId": accumulator.response_id,
            "modelVersion": accumulator.model_version,
        },
        final,
    )
    if accumulator.usage is not None:
        final["usage"] = accumulator.usage


__all__ = ["normalize_gemini_stream_response"]
