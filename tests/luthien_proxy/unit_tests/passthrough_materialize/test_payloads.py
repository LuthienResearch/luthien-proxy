from __future__ import annotations

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind, Provider
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalRequestInput,
    CanonicalResponseInput,
    ResponseEventType,
    build_request_event_payload,
    build_response_event_payload,
)


def test_builds_request_payload_with_metadata_and_native_request() -> None:
    endpoint = EligibleEndpoint(
        path="/openai/v1/chat/completions",
        provider=Provider.OPENAI,
        kind=EndpointKind.OPENAI_CHAT_COMPLETIONS,
    )
    provider_request = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    original_request = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    final_request = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}

    payload = build_request_event_payload(
        CanonicalRequestInput(
            endpoint=endpoint,
            is_streaming=False,
            final_model="gpt-4o-mini",
            original_request=original_request,
            final_request=final_request,
            provider_request=provider_request,
        )
    )

    assert payload["provider"] == "openai"
    assert payload["endpoint"] == "/openai/v1/chat/completions"
    assert payload["endpoint_kind"] == "openai_chat_completions"
    assert payload["is_streaming"] is False
    assert payload["final_model"] == "gpt-4o-mini"
    assert payload["original_request"] == original_request
    assert payload["final_request"] == final_request
    assert payload["provider_request"] == provider_request


def test_request_payload_is_independent_of_source_mutation() -> None:
    endpoint = EligibleEndpoint(
        path="/openai/v1/responses",
        provider=Provider.OPENAI,
        kind=EndpointKind.OPENAI_RESPONSES,
    )
    source_request = {"model": "gpt-4o", "input": [{"role": "user", "content": "hi"}]}
    input_data = CanonicalRequestInput(
        endpoint=endpoint,
        is_streaming=False,
        final_model="gpt-4o",
        original_request=source_request,
        final_request=source_request,
        provider_request=source_request,
    )
    source_request["model"] = "mutated"
    source_request["input"] = []

    payload = build_request_event_payload(input_data)

    assert payload["original_request"] == {"model": "gpt-4o", "input": [{"role": "user", "content": "hi"}]}
    assert payload["final_request"] == {"model": "gpt-4o", "input": [{"role": "user", "content": "hi"}]}
    assert payload["provider_request"] == {"model": "gpt-4o", "input": [{"role": "user", "content": "hi"}]}


def test_builds_non_streaming_response_payload_with_native_response() -> None:
    endpoint = EligibleEndpoint(
        path="/openai/v1/responses",
        provider=Provider.OPENAI,
        kind=EndpointKind.OPENAI_RESPONSES,
    )
    provider_response = {"id": "resp_123", "output": [{"type": "message", "content": []}]}
    final_response = {"id": "msg_123", "content": []}

    payload = build_response_event_payload(
        CanonicalResponseInput(
            endpoint=endpoint,
            is_streaming=False,
            final_model="gpt-4o",
            original_response=final_response,
            final_response=final_response,
            provider_response=provider_response,
        )
    )

    assert payload["event_type"] == ResponseEventType.NON_STREAMING.value
    assert payload["provider"] == "openai"
    assert payload["provider_response"] == provider_response
    assert payload["original_response"] == final_response
    assert payload["final_response"] == final_response


def test_builds_streaming_response_payload_with_native_response() -> None:
    endpoint = EligibleEndpoint(
        path="/gemini/v1beta/models/gemini-2.5-pro:streamGenerateContent",
        provider=Provider.GEMINI,
        kind=EndpointKind.GEMINI_STREAM_GENERATE_CONTENT,
    )
    provider_response = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
    final_response = {"content": [{"type": "text", "text": "hi"}]}

    payload = build_response_event_payload(
        CanonicalResponseInput(
            endpoint=endpoint,
            is_streaming=True,
            final_model="gemini-2.5-pro",
            original_response=final_response,
            final_response=final_response,
            provider_response=provider_response,
        )
    )

    assert payload["event_type"] == ResponseEventType.STREAMING.value
    assert payload["provider"] == "gemini"
    assert payload["endpoint_kind"] == "gemini_stream_generate_content"
    assert payload["is_streaming"] is True
    assert payload["provider_response"] == provider_response


def test_response_payload_is_independent_of_source_mutation() -> None:
    endpoint = EligibleEndpoint(
        path="/gemini/v1beta/models/gemini-2.5-pro:generateContent",
        provider=Provider.GEMINI,
        kind=EndpointKind.GEMINI_GENERATE_CONTENT,
    )
    source_response = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
    input_data = CanonicalResponseInput(
        endpoint=endpoint,
        is_streaming=False,
        final_model="gemini-2.5-pro",
        original_response=source_response,
        final_response=source_response,
        provider_response=source_response,
    )
    source_response["candidates"] = []

    payload = build_response_event_payload(input_data)

    assert payload["original_response"] == {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
    assert payload["final_response"] == {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
    assert payload["provider_response"] == {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
