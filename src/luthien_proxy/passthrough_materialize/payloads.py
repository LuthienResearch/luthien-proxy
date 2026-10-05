"""Canonical event payload builders for provider passthrough captures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TypedDict, assert_never

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Mapping[str, "JsonValue"] | Sequence["JsonValue"]
type JsonObject = Mapping[str, JsonValue]
type JsonMutableValue = JsonScalar | dict[str, "JsonMutableValue"] | list["JsonMutableValue"]
type JsonMutableObject = dict[str, JsonMutableValue]


class ResponseEventType(StrEnum):
    """Canonical response event names used by conversation history."""

    NON_STREAMING = "transaction.non_streaming_response_recorded"
    STREAMING = "transaction.streaming_response_recorded"


@dataclass(frozen=True, slots=True)
class CanonicalRequestInput:
    """Immutable request-side materialization input."""

    endpoint: EligibleEndpoint
    is_streaming: bool
    final_model: str | None
    original_request: JsonObject
    final_request: JsonObject
    provider_request: JsonObject

    def __post_init__(self) -> None:
        """Freeze nested JSON aliases after dataclass construction."""
        object.__setattr__(self, "original_request", _freeze_json_object(self.original_request))
        object.__setattr__(self, "final_request", _freeze_json_object(self.final_request))
        object.__setattr__(self, "provider_request", _freeze_json_object(self.provider_request))


@dataclass(frozen=True, slots=True)
class CanonicalResponseInput:
    """Immutable response-side materialization input."""

    endpoint: EligibleEndpoint
    is_streaming: bool
    final_model: str | None
    original_response: JsonObject
    final_response: JsonObject
    provider_response: JsonObject

    def __post_init__(self) -> None:
        """Freeze nested JSON aliases after dataclass construction."""
        object.__setattr__(self, "original_response", _freeze_json_object(self.original_response))
        object.__setattr__(self, "final_response", _freeze_json_object(self.final_response))
        object.__setattr__(self, "provider_response", _freeze_json_object(self.provider_response))


class CanonicalRequestPayload(TypedDict):
    """JSON-compatible transaction.request_recorded payload."""

    provider: str
    endpoint: str
    endpoint_kind: str
    is_streaming: bool
    final_model: str | None
    original_request: JsonMutableObject
    final_request: JsonMutableObject
    provider_request: JsonMutableObject


class CanonicalResponsePayload(TypedDict):
    """JSON-compatible transaction response payload."""

    event_type: str
    provider: str
    endpoint: str
    endpoint_kind: str
    is_streaming: bool
    final_model: str | None
    original_response: JsonMutableObject
    final_response: JsonMutableObject
    provider_response: JsonMutableObject


def build_request_event_payload(payload_input: CanonicalRequestInput) -> CanonicalRequestPayload:
    """Build the canonical request event payload from immutable input."""
    endpoint = payload_input.endpoint
    return {
        "provider": endpoint.provider.value,
        "endpoint": endpoint.path,
        "endpoint_kind": endpoint.kind.value,
        "is_streaming": payload_input.is_streaming,
        "final_model": payload_input.final_model,
        "original_request": _copy_json_object(payload_input.original_request),
        "final_request": _copy_json_object(payload_input.final_request),
        "provider_request": _copy_json_object(payload_input.provider_request),
    }


def build_response_event_payload(payload_input: CanonicalResponseInput) -> CanonicalResponsePayload:
    """Build the canonical response event payload from immutable input."""
    endpoint = payload_input.endpoint
    return {
        "event_type": _response_event_type(payload_input.is_streaming).value,
        "provider": endpoint.provider.value,
        "endpoint": endpoint.path,
        "endpoint_kind": endpoint.kind.value,
        "is_streaming": payload_input.is_streaming,
        "final_model": payload_input.final_model,
        "original_response": _copy_json_object(payload_input.original_response),
        "final_response": _copy_json_object(payload_input.final_response),
        "provider_response": _copy_json_object(payload_input.provider_response),
    }


def _response_event_type(is_streaming: bool) -> ResponseEventType:
    if is_streaming:
        return ResponseEventType.STREAMING
    return ResponseEventType.NON_STREAMING


def _freeze_json_object(value: JsonObject) -> JsonObject:
    return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})


def _freeze_json_value(value: JsonValue) -> JsonValue:
    match value:
        case None | str() | bool() | int() | float():
            return value
        case Mapping():
            return _freeze_json_object(value)
        case Sequence():
            return tuple(_freeze_json_value(item) for item in value)
        case unreachable:
            assert_never(unreachable)


def _copy_json_object(value: JsonObject) -> JsonMutableObject:
    return {key: _copy_json_value(item) for key, item in value.items()}


def _copy_json_value(value: JsonValue) -> JsonMutableValue:
    match value:
        case None | str() | bool() | int() | float():
            return value
        case Mapping():
            return _copy_json_object(value)
        case Sequence():
            return [_copy_json_value(item) for item in value]
        case unreachable:
            assert_never(unreachable)


__all__ = [
    "CanonicalRequestInput",
    "CanonicalRequestPayload",
    "CanonicalResponseInput",
    "CanonicalResponsePayload",
    "JsonMutableObject",
    "JsonMutableValue",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "ResponseEventType",
    "build_request_event_payload",
    "build_response_event_payload",
]
