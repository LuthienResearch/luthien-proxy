from __future__ import annotations

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind, Provider
from luthien_proxy.passthrough_materialize.gemini import normalize_gemini_request
from luthien_proxy.passthrough_materialize.openai import (
    normalize_openai_chat_request,
    normalize_openai_responses_request,
)
from luthien_proxy.passthrough_materialize.payloads import build_request_event_payload


def _chat_endpoint() -> EligibleEndpoint:
    return EligibleEndpoint(
        path="/openai/v1/chat/completions",
        provider=Provider.OPENAI,
        kind=EndpointKind.OPENAI_CHAT_COMPLETIONS,
    )


def _responses_endpoint() -> EligibleEndpoint:
    return EligibleEndpoint(
        path="/openai/v1/responses",
        provider=Provider.OPENAI,
        kind=EndpointKind.OPENAI_RESPONSES,
    )


def _gemini_endpoint() -> EligibleEndpoint:
    return EligibleEndpoint(
        path="/gemini/v1beta/models/gemini-2.5-pro:generateContent",
        provider=Provider.GEMINI,
        kind=EndpointKind.GEMINI_GENERATE_CONTENT,
    )


def test_normalizes_responses_reasoning_capture_when_unmodelled_items_surround_recoverable_turns() -> None:
    # Given a reasoning-model request that echoes opaque response items in input.
    request = {
        "model": "gpt-5.6-sol",
        "input": [
            {"type": "reasoning", "id": "rs_1", "summary": [], "content": []},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        ],
    }

    # When the request is materialized.
    payload = build_request_event_payload(
        normalize_openai_responses_request(_responses_endpoint(), request, transaction_id="txn_reasoning_request")
    )

    # Then only recoverable user and tool turns are retained.
    assert payload["final_request"]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]


def test_normalizes_responses_string_input_when_instructions_are_present() -> None:
    # Given a Responses request using the plain-string shorthand.
    request = {"model": "gpt-5.6-sol", "instructions": "Be concise.", "input": "hi"}

    # When the request is materialized.
    payload = build_request_event_payload(
        normalize_openai_responses_request(_responses_endpoint(), request, transaction_id="txn_string_input")
    )

    # Then instructions and the user message are preserved.
    assert payload["final_request"]["messages"] == [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "hi"},
    ]


def test_normalizes_chat_request_when_unknown_messages_and_provider_tools_are_present() -> None:
    # Given a Chat Completions request with an unmodelled message and provider tool.
    request = {
        "model": "gpt-5.6-sol",
        "messages": [
            {"role": "provider_internal", "content": "ignore"},
            {"role": "user", "content": "hi"},
        ],
        "tools": [{"type": "web_search"}],
    }

    # When the request is materialized.
    payload = build_request_event_payload(
        normalize_openai_chat_request(_chat_endpoint(), request, transaction_id="txn_lenient_chat")
    )

    # Then the recognized user message remains and unsupported input is omitted.
    assert payload["final_request"]["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["final_request"]["tools"] == []


def test_normalizes_gemini_request_when_unknown_parts_and_provider_tools_are_present() -> None:
    # Given a Gemini request with an opaque part and a non-function tool.
    request = {
        "contents": [
            {"role": "user", "parts": [{"thought": True}, {"text": "hi"}]},
            {"role": "provider_internal", "parts": [{"text": "ignore"}]},
        ],
        "tools": [{"googleSearch": {}}],
    }

    # When the request is materialized.
    payload = build_request_event_payload(
        normalize_gemini_request(_gemini_endpoint(), request, transaction_id="txn_lenient_gemini")
    )

    # Then the recognized text remains and unsupported input is omitted.
    assert payload["final_request"]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert payload["final_request"]["tools"] == []
