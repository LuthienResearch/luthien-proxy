from __future__ import annotations

import pytest

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind, Provider
from luthien_proxy.passthrough_materialize.openai import (
    PassthroughNormalizeError,
    PassthroughNormalizeReason,
    normalize_openai_chat_request,
    normalize_openai_chat_response,
    normalize_openai_responses_request,
    normalize_openai_responses_response,
)
from luthien_proxy.passthrough_materialize.payloads import (
    JsonObject,
    build_request_event_payload,
    build_response_event_payload,
)


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


def test_normalizes_chat_request_when_buffered_body_contains_tools_and_text_blocks() -> None:
    request = {
        "model": "gpt-4.1",
        "stream": False,
        "messages": [
            {"role": "developer", "content": "Follow policy."},
            {"role": "user", "content": [{"type": "text", "text": "Use the weather tool."}]},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": "auto",
        "max_completion_tokens": 128,
    }

    normalized = normalize_openai_chat_request(_chat_endpoint(), request, transaction_id="txn_1")
    payload = build_request_event_payload(normalized)

    assert normalized.is_streaming is False
    assert normalized.final_model == "gpt-4.1"
    assert payload["provider_request"] == request
    assert payload["final_request"] == {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": "Follow policy."},
            {"role": "user", "content": [{"type": "text", "text": "Use the weather tool."}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "weather",
                        "input": {"city": "Paris"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ],
        "tools": [
            {
                "name": "weather",
                "description": "Get weather",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 128,
        "max_completion_tokens": 128,
        "stream": False,
    }


def test_normalizes_chat_buffered_response_when_content_tool_refusal_and_usage_present() -> None:
    response = {
        "id": "chatcmpl_1",
        "model": "gpt-4.1",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "I can check.",
                    "refusal": "Cannot reveal hidden data.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"q":"safe"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    normalized = normalize_openai_chat_response(
        _chat_endpoint(), response, request_is_streaming=False, http_status=200, transaction_id="txn_2"
    )
    payload = build_response_event_payload(normalized)

    assert normalized.is_streaming is False
    assert normalized.final_model == "gpt-4.1"
    assert payload["provider_response"] == response
    assert payload["final_response"] == {
        "id": "chatcmpl_1",
        "model": "gpt-4.1",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I can check."},
            {"type": "text", "text": "Cannot reveal hidden data."},
            {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"q": "safe"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def test_normalizes_chat_stream_response_when_openai_sse_events_are_complete() -> None:
    response = {
        "stream_format": "openai-sse",
        "events": [
            {"id": "chatcmpl_2", "model": "gpt-4.1", "choices": [{"delta": {"role": "assistant", "content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        ],
        "final": {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    }

    normalized = normalize_openai_chat_response(
        _chat_endpoint(), response, request_is_streaming=True, http_status=200, transaction_id="txn_3"
    )
    payload = build_response_event_payload(normalized)

    assert normalized.is_streaming is True
    assert payload["final_response"] == {
        "id": "chatcmpl_2",
        "model": "gpt-4.1",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }


def test_normalizes_upstream_http_error_when_openai_error_body_is_captured() -> None:
    response = {"error": {"message": "bad request", "type": "invalid_request_error"}}

    normalized = normalize_openai_chat_response(
        _chat_endpoint(), response, request_is_streaming=False, http_status=400, transaction_id="txn_4"
    )
    payload = build_response_event_payload(normalized)

    assert payload["final_response"] == {
        "role": "assistant",
        "content": [],
        "stop_reason": "error",
        "error": {"status_code": 400, "body": response},
    }
    assert payload["provider_response"] == response


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"model": "gpt-4.1"}, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD),
        (
            {
                "model": "gpt-4.1",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}],
                    }
                ],
            },
            PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        ),
    ],
)
def test_chat_request_raises_typed_error_when_payload_is_malformed_or_unsupported(
    payload: JsonObject, reason: PassthroughNormalizeReason
) -> None:
    with pytest.raises(PassthroughNormalizeError) as exc_info:
        normalize_openai_chat_request(_chat_endpoint(), payload, transaction_id="txn_bad")

    assert exc_info.value.provider == Provider.OPENAI
    assert exc_info.value.endpoint_kind == EndpointKind.OPENAI_CHAT_COMPLETIONS
    assert exc_info.value.endpoint_path == "/openai/v1/chat/completions"
    assert exc_info.value.transaction_id == "txn_bad"
    assert exc_info.value.reason == reason


def test_stream_wrapper_raises_typed_error_when_capture_was_truncated() -> None:
    response = {"stream_format": "openai-sse", "events": [], "final": None, "capture_truncated": True}

    with pytest.raises(PassthroughNormalizeError) as exc_info:
        normalize_openai_chat_response(
            _chat_endpoint(), response, request_is_streaming=True, http_status=200, transaction_id="txn_truncated"
        )

    assert exc_info.value.reason == PassthroughNormalizeReason.CAPTURE_TRUNCATED


def test_normalizes_responses_request_when_instructions_input_tools_and_tokens_present() -> None:
    request = {
        "model": "gpt-4.1",
        "instructions": "Be concise.",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Summarize this."}]},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        ],
        "tools": [
            {
                "type": "function",
                "name": "search",
                "description": "Search",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ],
        "tool_choice": "auto",
        "max_output_tokens": 64,
        "stream": True,
    }

    normalized = normalize_openai_responses_request(_responses_endpoint(), request, transaction_id="txn_5")
    payload = build_request_event_payload(normalized)

    assert normalized.is_streaming is True
    assert payload["final_request"] == {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": [{"type": "text", "text": "Summarize this."}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "done"},
        ],
        "tools": [
            {
                "name": "search",
                "description": "Search",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 64,
        "max_output_tokens": 64,
        "stream": True,
    }


def test_normalizes_responses_buffered_response_when_output_status_function_and_usage_present() -> None:
    response = {
        "id": "resp_1",
        "model": "gpt-4.1",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Done."},
                    {"type": "refusal", "refusal": "Cannot provide secret."},
                ],
            },
            {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": '{"query":"x"}'},
        ],
        "usage": {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11},
    }

    normalized = normalize_openai_responses_response(
        _responses_endpoint(), response, request_is_streaming=False, http_status=200, transaction_id="txn_6"
    )
    payload = build_response_event_payload(normalized)

    assert payload["final_response"] == {
        "id": "resp_1",
        "model": "gpt-4.1",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Done."},
            {"type": "text", "text": "Cannot provide secret."},
            {"type": "tool_use", "id": "call_1", "name": "search", "input": {"query": "x"}},
        ],
        "stop_reason": "tool_use",
        "status": "completed",
        "usage": {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11},
    }


def test_responses_buffered_response_with_novel_status_raises_typed_error_not_uncaught() -> None:
    # A future OpenAI Response.status the pinned SDK does not model must surface as a
    # typed, retryable PassthroughNormalizeError (skips one transaction), NOT an uncaught
    # pydantic ValidationError -- materialize.py and reconcile.py catch only typed/DB
    # errors, so an uncaught ValidationError would wedge the whole backfill batch.
    response = {
        "id": "resp_1",
        "model": "gpt-4.1",
        "status": "a_future_status_the_pinned_sdk_does_not_know",
        "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
        ],
    }

    with pytest.raises(PassthroughNormalizeError) as exc_info:
        normalize_openai_responses_response(
            _responses_endpoint(),
            response,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_novel_status",
        )

    assert exc_info.value.detail == "response"


def test_normalizes_responses_stream_response_when_events_fold_to_final_message() -> None:
    response = {
        "stream_format": "openai-sse",
        "events": [
            {"type": "response.output_text.delta", "delta": "Hel"},
            {"type": "response.output_text.delta", "delta": "lo"},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_2",
                    "model": "gpt-4.1",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                },
            },
        ],
        "final": {"type": "response.completed"},
    }

    normalized = normalize_openai_responses_response(
        _responses_endpoint(), response, request_is_streaming=True, http_status=200, transaction_id="txn_7"
    )
    payload = build_response_event_payload(normalized)

    assert payload["final_response"] == {
        "id": "resp_2",
        "model": "gpt-4.1",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello"}],
        "stop_reason": "end_turn",
        "status": "completed",
        "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    }


def test_responses_stream_uses_completed_response_when_lifecycle_events_are_content_free() -> None:
    response = {
        "stream_format": "openai-sse",
        "events": [
            {"type": "response.created", "response": {"id": "resp_stream", "status": "in_progress"}},
            {"type": "response.in_progress", "response": {"id": "resp_stream", "status": "in_progress"}},
            {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "msg_1"}},
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text"},
            },
            {"type": "response.output_text.delta", "delta": "Draft"},
            {"type": "response.output_text.done", "output_index": 0, "content_index": 0},
            {"type": "response.content_part.done", "output_index": 0, "content_index": 0},
            {"type": "response.output_item.done", "output_index": 0},
            {"type": "response.output_item.added", "output_index": 1, "item": {"type": "function_call", "id": "fc_1"}},
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"query"'},
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": ':"safe"}'},
            {"type": "response.function_call_arguments.done", "output_index": 1},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_stream",
                    "model": "gpt-4.1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Authoritative final."}],
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_search",
                            "name": "search",
                            "arguments": '{"query":"safe"}',
                        },
                    ],
                    "usage": {"input_tokens": 0, "output_tokens": 4, "total_tokens": 4},
                },
            },
        ],
        "final": {"type": "response.completed"},
    }

    normalized = normalize_openai_responses_response(
        _responses_endpoint(),
        response,
        request_is_streaming=True,
        http_status=200,
        transaction_id="txn_stream_lifecycle",
    )
    payload = build_response_event_payload(normalized)

    assert payload["final_response"] == {
        "id": "resp_stream",
        "model": "gpt-4.1",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Authoritative final."},
            {"type": "tool_use", "id": "call_search", "name": "search", "input": {"query": "safe"}},
        ],
        "stop_reason": "tool_use",
        "status": "completed",
        "usage": {"input_tokens": 0, "output_tokens": 4, "total_tokens": 4},
    }


def test_stream_wrappers_raise_typed_error_when_raw_partial_capture_is_present() -> None:
    chat_response = {
        "stream_format": "openai-sse",
        "events": [{"choices": [{"delta": {"content": "Hel"}}]}],
        "raw": "data: {broken",
        "final": None,
    }
    responses_response = {
        "stream_format": "openai-sse",
        "events": [{"type": "response.output_text.delta", "delta": "Hel"}],
        "raw": "data: {broken",
        "final": None,
    }

    for endpoint, normalizer, response in (
        (_chat_endpoint(), normalize_openai_chat_response, chat_response),
        (_responses_endpoint(), normalize_openai_responses_response, responses_response),
    ):
        with pytest.raises(PassthroughNormalizeError) as exc_info:
            normalizer(endpoint, response, request_is_streaming=True, http_status=200, transaction_id="txn_raw")

        assert exc_info.value.reason == PassthroughNormalizeReason.CAPTURE_TRUNCATED


def test_chat_stream_reconstructs_tool_call_arguments_and_refusal_deltas() -> None:
    tool_response = {
        "stream_format": "openai-sse",
        "events": [
            {
                "id": "chatcmpl_tool",
                "model": "gpt-4.1",
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_weather",
                                    "type": "function",
                                    "function": {"name": "weather", "arguments": '{"city"'},
                                }
                            ],
                        }
                    }
                ],
            },
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ':"Paris"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ],
        "final": {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    }
    refusal_response = {
        "stream_format": "openai-sse",
        "events": [
            {"id": "chatcmpl_refusal", "model": "gpt-4.1", "choices": [{"delta": {"refusal": "No"}}]},
            {"choices": [{"delta": {"refusal": "."}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ],
        "final": {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    }

    tool_payload = build_response_event_payload(
        normalize_openai_chat_response(
            _chat_endpoint(),
            tool_response,
            request_is_streaming=True,
            http_status=200,
            transaction_id="txn_tool_stream",
        )
    )
    refusal_payload = build_response_event_payload(
        normalize_openai_chat_response(
            _chat_endpoint(),
            refusal_response,
            request_is_streaming=True,
            http_status=200,
            transaction_id="txn_refusal_stream",
        )
    )

    assert tool_payload["final_response"] == {
        "id": "chatcmpl_tool",
        "model": "gpt-4.1",
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "call_weather", "name": "weather", "input": {"city": "Paris"}}],
        "stop_reason": "tool_use",
    }
    assert refusal_payload["final_response"] == {
        "id": "chatcmpl_refusal",
        "model": "gpt-4.1",
        "role": "assistant",
        "content": [{"type": "text", "text": "No."}],
        "stop_reason": "end_turn",
    }


def test_normalizers_reject_missing_or_empty_tool_call_ids() -> None:
    chat_response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [{"type": "function", "function": {"name": "lookup", "arguments": "{}"}}],
                },
            }
        ]
    }
    responses_response = {"output": [{"type": "function_call", "call_id": "", "name": "lookup", "arguments": "{}"}]}

    for endpoint, normalizer, response in (
        (_chat_endpoint(), normalize_openai_chat_response, chat_response),
        (_responses_endpoint(), normalize_openai_responses_response, responses_response),
    ):
        with pytest.raises(PassthroughNormalizeError) as exc_info:
            normalizer(
                endpoint, response, request_is_streaming=False, http_status=200, transaction_id="txn_missing_tool_id"
            )

        assert exc_info.value.reason == PassthroughNormalizeReason.MISSING_REQUIRED_FIELD
        assert "id" in exc_info.value.detail or "call_id" in exc_info.value.detail


def test_responses_unknown_variants_report_precise_typed_details() -> None:
    unknown_input = {"model": "gpt-4.1", "input": [{"type": "web_search_call", "query": "x"}]}
    unknown_event = {
        "stream_format": "openai-sse",
        "events": [{"type": "response.unexpected", "delta": "x"}],
        "final": None,
    }

    with pytest.raises(PassthroughNormalizeError) as input_exc:
        normalize_openai_responses_request(_responses_endpoint(), unknown_input, transaction_id="txn_unknown_input")

    with pytest.raises(PassthroughNormalizeError) as event_exc:
        normalize_openai_responses_response(
            _responses_endpoint(),
            unknown_event,
            request_is_streaming=True,
            http_status=200,
            transaction_id="txn_unknown_event",
        )

    assert input_exc.value.reason == PassthroughNormalizeReason.MISSING_REQUIRED_FIELD
    assert input_exc.value.detail == "input"
    assert event_exc.value.reason == PassthroughNormalizeReason.UNSUPPORTED_VARIANT
    assert event_exc.value.detail == "stream event.type:response.unexpected"


def test_usage_zero_counts_and_unknown_finish_reasons_do_not_leak_raw_values() -> None:
    response = {
        "id": "chatcmpl_zero",
        "model": "gpt-4.1",
        "choices": [{"finish_reason": "new_provider_reason", "message": {"role": "assistant", "content": "Done"}}],
        "usage": {"prompt_tokens": 0, "input_tokens": 9, "completion_tokens": 0, "output_tokens": 8, "total_tokens": 0},
    }

    payload = build_response_event_payload(
        normalize_openai_chat_response(
            _chat_endpoint(), response, request_is_streaming=False, http_status=200, transaction_id="txn_zero_usage"
        )
    )

    assert payload["final_response"]["usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert payload["final_response"]["stop_reason"] == "end_turn"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {"model": "gpt-4.1", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "x"}]}]},
            PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        ),
        (
            {"id": "resp_3", "model": "gpt-4.1", "output": [{"type": "web_search_call"}]},
            PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        ),
    ],
)
def test_responses_payload_raises_typed_error_when_sub_shape_is_unknown(
    payload: JsonObject, reason: PassthroughNormalizeReason
) -> None:
    endpoint = _responses_endpoint()

    with pytest.raises(PassthroughNormalizeError) as exc_info:
        if "input" in payload:
            normalize_openai_responses_request(endpoint, payload, transaction_id="txn_unsupported")
        else:
            normalize_openai_responses_response(
                endpoint, payload, request_is_streaming=False, http_status=200, transaction_id="txn_unsupported"
            )

    assert exc_info.value.reason == reason
    assert exc_info.value.endpoint_kind == EndpointKind.OPENAI_RESPONSES


def test_chat_reasoning_tokens_lift_from_completion_tokens_details_into_canonical_usage() -> None:
    # Given: an OpenAI Chat Completions response for a reasoning model. OpenAI nests the
    # reasoning token count under `usage.completion_tokens_details.reasoning_tokens` -
    # `output_tokens` still counts them, but we surface `reasoning_tokens` separately so
    # downstream consumers can distinguish reasoning from visible output.
    response = {
        "id": "chatcmpl_reasoning",
        "model": "gpt-5.6-sol",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "visible"}}],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
            "completion_tokens_details": {"reasoning_tokens": 6},
        },
    }

    payload = build_response_event_payload(
        normalize_openai_chat_response(
            _chat_endpoint(),
            response,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_chat_reasoning",
        )
    )

    assert payload["final_response"]["usage"] == {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
        "reasoning_tokens": 6,
    }


def test_responses_reasoning_tokens_lift_from_output_tokens_details_into_canonical_usage() -> None:
    # Given: an OpenAI Responses API response for a reasoning model. Responses nests the
    # reasoning token count under `usage.output_tokens_details.reasoning_tokens`.
    response = {
        "id": "resp_reasoning",
        "model": "gpt-5.6-sol",
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "visible"}]}],
        "usage": {
            "input_tokens": 14,
            "output_tokens": 9,
            "total_tokens": 23,
            "output_tokens_details": {"reasoning_tokens": 7},
        },
    }

    payload = build_response_event_payload(
        normalize_openai_responses_response(
            _responses_endpoint(),
            response,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_responses_reasoning",
        )
    )

    assert payload["final_response"]["usage"] == {
        "input_tokens": 14,
        "output_tokens": 9,
        "total_tokens": 23,
        "reasoning_tokens": 7,
    }


def test_chat_content_filter_finish_reason_maps_to_safety_not_end_turn() -> None:
    # Given: OpenAI's `content_filter` finish_reason indicates a safety-policy block, NOT
    # a normal completion. Previously we collapsed it to `end_turn`, making it
    # indistinguishable from a natural stop. Now it maps to `safety` (matching the Gemini
    # normalizer's SAFETY bucket) so downstream consumers can filter blocked completions.
    response = {
        "id": "chatcmpl_blocked",
        "model": "gpt-4.1",
        "choices": [{"finish_reason": "content_filter", "message": {"role": "assistant", "content": ""}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
    }

    payload = build_response_event_payload(
        normalize_openai_chat_response(
            _chat_endpoint(),
            response,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_content_filter",
        )
    )

    assert payload["final_response"]["stop_reason"] == "safety"
