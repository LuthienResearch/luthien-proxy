"""Shared strict helpers for Gemini passthrough normalization."""

from __future__ import annotations

from collections.abc import Mapping

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind, Provider
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    fail,
    is_json_object,
    json_mutable_object,
    optional_string,
)
from luthien_proxy.passthrough_materialize.payloads import JsonMutableObject, JsonMutableValue, JsonObject, JsonValue


def gemini_endpoint_streaming(endpoint: EligibleEndpoint, transaction_id: str | None) -> bool:
    """Return True when the eligible Gemini endpoint is the streaming variant."""
    match endpoint.provider:
        case Provider.GEMINI:
            pass
        case _:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_ENDPOINT, "provider mismatch")
    match endpoint.kind:
        case EndpointKind.GEMINI_GENERATE_CONTENT:
            return False
        case EndpointKind.GEMINI_STREAM_GENERATE_CONTENT:
            return True
        case _:
            fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_ENDPOINT, "endpoint kind mismatch")


def gemini_model_from_path(path: str) -> str | None:
    """Extract the Gemini model id from a captured request path."""
    marker = "models/"
    if marker not in path:
        return None
    return path.split(marker, maxsplit=1)[1].split(":", maxsplit=1)[0] or None


def gemini_function_call_id(call: Mapping[str, JsonValue], name: str, ordinal: int) -> str:
    """Return a provider call id or the deterministic identity for an id-less call."""
    provider_id = optional_string(call, "id")
    return provider_id or f"gemini:{name}:{ordinal}"


def gemini_function_call(
    endpoint: EligibleEndpoint,
    call: Mapping[str, JsonValue],
    ordinal: int,
    transaction_id: str | None,
) -> JsonMutableObject:
    """Normalize a Gemini functionCall using its provider id or per-turn identity."""
    name = optional_string(call, "name")
    if name is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "functionCall.name")
    args = call.get("args")
    call_id = gemini_function_call_id(call, name, ordinal)
    if args is None:
        return {"type": "tool_use", "id": call_id, "name": name, "input": {}}
    if not is_json_object(args):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "functionCall.args")
    return {"type": "tool_use", "id": call_id, "name": name, "input": json_mutable_object(args)}


def gemini_usage(endpoint: EligibleEndpoint, value: JsonValue, transaction_id: str | None) -> JsonMutableObject:
    """Convert Gemini usageMetadata into canonical token usage counts."""
    if not is_json_object(value):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "usageMetadata")
    result: JsonMutableObject = {}
    for source_key, target_key in (
        ("promptTokenCount", "input_tokens"),
        ("candidatesTokenCount", "output_tokens"),
        ("totalTokenCount", "total_tokens"),
        ("cachedContentTokenCount", "cache_read_input_tokens"),
        ("thoughtsTokenCount", "reasoning_tokens"),
    ):
        token_count = value.get(source_key)
        if token_count is None:
            continue
        if not isinstance(token_count, int) or isinstance(token_count, bool):
            fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, f"usageMetadata.{source_key}")
        result[target_key] = token_count
    return result


def gemini_stop_reason(endpoint: EligibleEndpoint, reason: str, transaction_id: str | None) -> str:
    """Map a Gemini finishReason to a canonical stop reason."""
    match reason:
        case "STOP":
            return "end_turn"
        case "MAX_TOKENS":
            return "max_tokens"
        case "SAFETY":
            return "safety"
        case "RECITATION" | "LANGUAGE":
            return "refusal"
        case "BLOCKLIST" | "PROHIBITED_CONTENT" | "SPII" | "IMAGE_SAFETY" | "IMAGE_PROHIBITED_CONTENT":
            return "blocked"
        case "MALFORMED_FUNCTION_CALL" | "UNEXPECTED_TOOL_CALL" | "TOO_MANY_TOOL_CALLS" | "MISSING_THOUGHT_SIGNATURE":
            return "error"
        case "FINISH_REASON_UNSPECIFIED" | "OTHER":
            return "error"
        case _:
            fail(
                endpoint,
                transaction_id,
                PassthroughNormalizeReason.UNSUPPORTED_VARIANT,
                f"candidate.finishReason:{reason}",
            )


def gemini_content_free_parts(
    endpoint: EligibleEndpoint, stop_reason: str, transaction_id: str | None
) -> list[JsonMutableValue]:
    """Return an empty assistant turn only for terminal safety outcomes."""
    if stop_reason not in {"safety", "refusal", "blocked"}:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "candidate.content")
    return []


def gemini_response_identifiers(response: JsonObject, final: JsonMutableObject) -> None:
    """Copy Gemini response id and model version into the canonical final object."""
    response_id = response.get("responseId")
    if isinstance(response_id, str):
        final["id"] = response_id
    model_version = response.get("modelVersion")
    if isinstance(model_version, str):
        final["model"] = model_version
