from __future__ import annotations

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind, Provider
from luthien_proxy.passthrough_materialize.openai import normalize_openai_responses_response
from luthien_proxy.passthrough_materialize.payloads import build_response_event_payload


def _responses_endpoint() -> EligibleEndpoint:
    return EligibleEndpoint(
        path="/openai/v1/responses",
        provider=Provider.OPENAI,
        kind=EndpointKind.OPENAI_RESPONSES,
    )


def test_normalizes_reasoning_output_with_text_and_function_calls() -> None:
    # Given a gpt-5.6-sol response with an opaque reasoning item.
    response = {
        "id": "resp_reasoning",
        "model": "gpt-5.6-sol",
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "content": [],
                "summary": [],
                "encrypted_content": "opaque",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I found two results."}],
            },
            {
                "type": "function_call",
                "call_id": "call_one",
                "name": "lookup",
                "arguments": '{"query":"first"}',
            },
            {
                "type": "function_call",
                "call_id": "call_two",
                "name": "lookup",
                "arguments": '{"query":"second"}',
            },
        ],
    }

    # When the materializer normalizes the captured response.
    payload = build_response_event_payload(
        normalize_openai_responses_response(
            _responses_endpoint(),
            response,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_reasoning",
        )
    )

    # Then it preserves usable assistant text and tool calls.
    assert payload["final_response"]["content"] == [
        {"type": "text", "text": "I found two results."},
        {"type": "tool_use", "id": "call_one", "name": "lookup", "input": {"query": "first"}},
        {"type": "tool_use", "id": "call_two", "name": "lookup", "input": {"query": "second"}},
    ]


def test_skips_unknown_output_item_when_response_has_usable_text() -> None:
    # Given a future output item adjacent to a standard output message.
    response = {
        "id": "resp_future",
        "model": "gpt-5.6-sol",
        "status": "completed",
        "output": [
            {"type": "future_provider_item", "provider_metadata": {"version": 1}},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Still usable."}],
            },
        ],
    }

    # When the materializer normalizes the captured response.
    payload = build_response_event_payload(
        normalize_openai_responses_response(
            _responses_endpoint(),
            response,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_future_output",
        )
    )

    # Then it skips the unknown item instead of failing the transaction.
    assert payload["final_response"]["content"] == [{"type": "text", "text": "Still usable."}]
