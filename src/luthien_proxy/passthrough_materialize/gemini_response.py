"""Gemini generateContent response normalization."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint
from luthien_proxy.passthrough_materialize.gemini_common import (
    gemini_content_free_parts,
    gemini_endpoint_streaming,
    gemini_function_call,
    gemini_response_identifiers,
    gemini_stop_reason,
    gemini_usage,
)
from luthien_proxy.passthrough_materialize.gemini_stream import normalize_gemini_stream_response
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    error_response,
    fail,
    is_json_object,
    is_json_sequence,
    json_mutable,
    json_mutable_object,
    optional_string,
    sequence_field,
)
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalResponseInput,
    JsonMutableObject,
    JsonMutableValue,
    JsonObject,
)
from luthien_proxy.passthrough_materialize.provider_models import parse_gemini_response


def normalize_gemini_response(
    endpoint: EligibleEndpoint,
    response: JsonObject,
    *,
    request_is_streaming: bool,
    http_status: int,
    transaction_id: str | None = None,
) -> CanonicalResponseInput:
    """Normalize a Gemini generateContent response into canonical response input."""
    endpoint_streaming = gemini_endpoint_streaming(endpoint, transaction_id)
    if endpoint_streaming != request_is_streaming:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_ENDPOINT, "streaming metadata mismatch")
    if http_status >= 400:
        final_response = error_response(http_status, response)
    elif request_is_streaming:
        final_response = normalize_gemini_stream_response(endpoint, response, transaction_id)
    else:
        try:
            source = parse_gemini_response(response).model_dump(mode="json", by_alias=True, exclude_none=True)
        except ValidationError:
            # Best-effort SDK: the google-genai response model is strict
            # (extra='forbid') and lags the live REST API (e.g. it rejects
            # usageMetadata.serviceTier); fall back to the raw payload, which
            # _buffered_response maps leniently.
            source = response
        final_response = _buffered_response(endpoint, source, transaction_id)
    model = final_response.get("model")
    final_model = model if isinstance(model, str) else None
    return CanonicalResponseInput(endpoint, request_is_streaming, final_model, final_response, final_response, response)


def _buffered_response(
    endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None
) -> JsonMutableObject:
    prompt_feedback = response.get("promptFeedback")
    if prompt_feedback is not None:
        if not is_json_object(prompt_feedback):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "promptFeedback")
        return _blocked_response(endpoint, response, prompt_feedback, transaction_id)
    return _candidate_response(endpoint, response, _zero_candidate(endpoint, response, transaction_id), transaction_id)


def _zero_candidate(endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None) -> JsonObject:
    for candidate in sequence_field(endpoint, response, "candidates", transaction_id):
        if not is_json_object(candidate):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate")
        index = candidate.get("index", 0)
        if not isinstance(index, int) or isinstance(index, bool):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.index")
        if index == 0:
            return candidate
    fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "candidates[0]")


def _blocked_response(
    endpoint: EligibleEndpoint, response: JsonObject, feedback: JsonObject, transaction_id: str | None
) -> JsonMutableObject:
    block_reason = optional_string(feedback, "blockReason")
    if block_reason is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "promptFeedback.blockReason")
    final: JsonMutableObject = {"role": "assistant", "content": [], "stop_reason": "blocked"}
    gemini_response_identifiers(response, final)
    _add_usage(endpoint, response, final, transaction_id)
    final["prompt_feedback"] = json_mutable_object(feedback)
    return final


def _candidate_response(
    endpoint: EligibleEndpoint, response: JsonObject, candidate: JsonObject, transaction_id: str | None
) -> JsonMutableObject:
    finish_reason = optional_string(candidate, "finishReason")
    if finish_reason is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "candidate.finishReason")
    content = candidate.get("content")
    stop_reason = gemini_stop_reason(endpoint, finish_reason, transaction_id)
    if content is None:
        parts = gemini_content_free_parts(endpoint, stop_reason, transaction_id)
    elif not is_json_object(content):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "candidate.content")
    else:
        role = optional_string(content, "role")
        match role:
            case "model":
                pass
            case None:
                fail(
                    endpoint,
                    transaction_id,
                    PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
                    "candidate.content.role",
                )
            case _:
                fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_VARIANT, "candidate.content.role")
        parts = _candidate_parts(endpoint, content, transaction_id)
        if not parts:
            parts = gemini_content_free_parts(endpoint, stop_reason, transaction_id)
    final: JsonMutableObject = {
        "role": "assistant",
        "content": parts,
        "stop_reason": stop_reason,
    }
    gemini_response_identifiers(response, final)
    _add_usage(endpoint, response, final, transaction_id)
    safety_ratings = candidate.get("safetyRatings")
    if safety_ratings is not None:
        if not is_json_sequence(safety_ratings):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.safetyRatings")
        final["safety_ratings"] = json_mutable(safety_ratings)
    return final


def _candidate_parts(
    endpoint: EligibleEndpoint, content: JsonObject, transaction_id: str | None
) -> list[JsonMutableValue]:
    parts = sequence_field(endpoint, content, "parts", transaction_id)
    result: list[JsonMutableValue] = []
    function_ordinal = 0
    for part in parts:
        if not is_json_object(part):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "candidate.part")
        match part:
            case {"text": str() as text}:
                result.append({"type": "text", "text": text})
            case {"functionCall": Mapping() as call}:
                result.append(gemini_function_call(endpoint, call, function_ordinal, transaction_id))
                function_ordinal += 1
            case {"functionResponse": _}:
                continue
            case _:
                continue
    return result


def _add_usage(
    endpoint: EligibleEndpoint, response: JsonObject, final: JsonMutableObject, transaction_id: str | None
) -> None:
    usage_metadata = response.get("usageMetadata")
    if usage_metadata is not None:
        final["usage"] = gemini_usage(endpoint, usage_metadata, transaction_id)


__all__ = ["normalize_gemini_response"]
