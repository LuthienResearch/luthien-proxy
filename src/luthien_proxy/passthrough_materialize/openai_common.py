"""Shared private helpers for OpenAI passthrough normalization."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, TypeGuard

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind, Provider
from luthien_proxy.passthrough_materialize.payloads import JsonMutableObject, JsonMutableValue, JsonObject, JsonValue


class PassthroughNormalizeReason(StrEnum):
    """Stable reason codes for retryable passthrough normalization failures."""

    MISSING_REQUIRED_FIELD = "missing_required_field"
    MALFORMED_JSON = "malformed_json"
    MALFORMED_PAYLOAD = "malformed_payload"
    UNSUPPORTED_ENDPOINT = "unsupported_endpoint"
    UNSUPPORTED_VARIANT = "unsupported_variant"
    CAPTURE_TRUNCATED = "capture_truncated"


@dataclass(frozen=True, slots=True)
class PassthroughNormalizeError(Exception):
    """Typed failure raised when eligible passthrough JSON cannot become canonical."""

    provider: Provider
    endpoint_kind: EndpointKind
    endpoint_path: str
    transaction_id: str | None
    reason: PassthroughNormalizeReason
    detail: str

    def __str__(self) -> str:
        """Return a stable diagnostic string for logs and reports."""
        tx = self.transaction_id or "<unknown>"
        return f"{self.provider.value}:{self.endpoint_kind.value}:{tx}:{self.reason.value}:{self.detail}"


def fail(
    endpoint: EligibleEndpoint, transaction_id: str | None, reason: PassthroughNormalizeReason, detail: str
) -> NoReturn:
    """Raise a typed passthrough normalization error."""
    raise PassthroughNormalizeError(
        provider=endpoint.provider,
        endpoint_kind=endpoint.kind,
        endpoint_path=endpoint.path,
        transaction_id=transaction_id,
        reason=reason,
        detail=detail,
    )


def is_json_object(value: JsonValue) -> TypeGuard[JsonObject]:
    """Return whether a JSON value is an object."""
    return isinstance(value, Mapping)


def is_json_sequence(value: JsonValue) -> TypeGuard[Sequence[JsonValue]]:
    """Return whether a JSON value is a non-string array."""
    return isinstance(value, Sequence) and not isinstance(value, str)


def require_openai_endpoint(endpoint: EligibleEndpoint, kind: EndpointKind, transaction_id: str | None) -> None:
    """Ensure a normalizer is used with its matching OpenAI endpoint kind."""
    if endpoint.provider != Provider.OPENAI or endpoint.kind != kind:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.UNSUPPORTED_ENDPOINT, "endpoint kind mismatch")


def sequence_field(
    endpoint: EligibleEndpoint, value: JsonObject, key: str, transaction_id: str | None
) -> Sequence[JsonValue]:
    """Return a required JSON array field or raise a typed error."""
    item = value.get(key)
    if is_json_sequence(item):
        return item
    fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, key)


def optional_string(value: JsonObject, key: str) -> str | None:
    """Return an optional JSON string field."""
    item = value.get(key)
    return item if isinstance(item, str) else None


def json_object_from_string(endpoint: EligibleEndpoint, raw: str, transaction_id: str | None) -> JsonMutableObject:
    """Parse a JSON object encoded as a string or raise a typed error."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_JSON, "function arguments")
    if is_json_object(parsed):
        return json_mutable_object(parsed)
    fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_JSON, "function arguments not object")


def json_mutable_object(value: Mapping[str, JsonValue]) -> JsonMutableObject:
    """Deep-copy a JSON object into mutable JSON-compatible containers."""
    return {key: json_mutable(item) for key, item in value.items()}


def json_mutable(value: JsonValue) -> JsonMutableValue:
    """Deep-copy a JSON value into mutable JSON-compatible containers."""
    match value:
        case None | str() | bool() | int() | float():
            return value
        case Mapping():
            return json_mutable_object(value)
        case Sequence():
            return [json_mutable(item) for item in value]


def lenient_text_content_from_openai(content: JsonValue) -> str | list[JsonMutableValue] | None:
    """Return recoverable text content while omitting unknown request blocks."""
    match content:
        case str():
            return content
        case Sequence() if not isinstance(content, str):
            blocks: list[JsonMutableValue] = []
            for block in content:
                match block:
                    case {"type": "text" | "input_text" | "output_text", "text": str() as text}:
                        blocks.append({"type": "text", "text": text})
                    case _:
                        continue
            return blocks or None
        case _:
            return None


def canonical_usage(usage: JsonValue) -> JsonMutableObject | None:
    """Map OpenAI token counters into canonical usage counters."""
    if not isinstance(usage, Mapping):
        return None
    input_tokens = _first_int(usage, "prompt_tokens", "input_tokens")
    output_tokens = _first_int(usage, "completion_tokens", "output_tokens")
    result: JsonMutableObject = {}
    if isinstance(input_tokens, int):
        result["input_tokens"] = input_tokens
    if isinstance(output_tokens, int):
        result["output_tokens"] = output_tokens
    total_tokens = usage.get("total_tokens")
    if isinstance(total_tokens, int):
        result["total_tokens"] = total_tokens
    # Reasoning-model token accounting: Chat Completions nests under
    # `completion_tokens_details.reasoning_tokens`; Responses nests under
    # `output_tokens_details.reasoning_tokens`. Reasoning tokens are
    # already counted inside `output_tokens`; we surface them separately so
    # downstream consumers can distinguish reasoning from visible output.
    reasoning_tokens = _reasoning_tokens(usage)
    if isinstance(reasoning_tokens, int):
        result["reasoning_tokens"] = reasoning_tokens
    return result or None


def _reasoning_tokens(usage: Mapping[str, JsonValue]) -> int | None:
    for details_key in ("completion_tokens_details", "output_tokens_details"):
        details = usage.get(details_key)
        if not isinstance(details, Mapping):
            continue
        reasoning = details.get("reasoning_tokens")
        if not isinstance(reasoning, int) or isinstance(reasoning, bool):
            continue
        # Non-reasoning models omit the field entirely at the API level; the SDK
        # parser (provider_models.parse_openai_response) injects `0` as a default
        # to satisfy required-field validation. Treat 0 as absent so we don't
        # pollute non-reasoning-model outputs with a spurious `reasoning_tokens: 0`.
        if reasoning == 0:
            continue
        return reasoning
    return None
    for details_key in ("completion_tokens_details", "output_tokens_details"):
        details = usage.get(details_key)
        if not isinstance(details, Mapping):
            continue
        reasoning = details.get("reasoning_tokens")
        if isinstance(reasoning, int) and not isinstance(reasoning, bool):
            return reasoning
    return None


def _first_int(usage: Mapping[str, JsonValue], first_key: str, second_key: str) -> int | None:
    first = usage.get(first_key)
    if isinstance(first, int):
        return first
    second = usage.get(second_key)
    return second if isinstance(second, int) else None


def error_response(http_status: int, response: JsonObject) -> JsonMutableObject:
    """Build a canonical upstream-error response without synthetic text."""
    return {
        "role": "assistant",
        "content": [],
        "stop_reason": "error",
        "error": {"status_code": http_status, "body": json_mutable_object(response)},
    }


def ensure_not_truncated(endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None) -> None:
    """Reject known-truncated stream captures before canonicalization."""
    if response.get("capture_truncated") is True or "raw" in response:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.CAPTURE_TRUNCATED, "stream capture truncated")


def stop_reason(reason: str | None) -> str:
    """Map OpenAI finish reasons to canonical stop reasons."""
    match reason:
        case "tool_calls":
            return "tool_use"
        case "length":
            return "max_tokens"
        case "content_filter":
            # OpenAI's content_filter is a safety-policy block. Map to the same
            # canonical safety bucket the Gemini normalizer uses so downstream
            # consumers can distinguish real completions from safety-blocked ones.
            return "safety"
        case "stop" | None:
            return "end_turn"
        case _:
            return "end_turn"
