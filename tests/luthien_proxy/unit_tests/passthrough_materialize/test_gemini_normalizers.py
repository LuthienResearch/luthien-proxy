from __future__ import annotations

import pytest

from luthien_proxy.passthrough_capture import reassemble_gemini_json_array_stream
from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind, Provider
from luthien_proxy.passthrough_materialize.gemini import (
    PassthroughNormalizeError,
    PassthroughNormalizeReason,
    normalize_gemini_request,
    normalize_gemini_response,
)
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalResponsePayload,
    JsonObject,
    build_request_event_payload,
    build_response_event_payload,
)


def _generate_content_endpoint() -> EligibleEndpoint:
    return EligibleEndpoint(
        path="/gemini/v1beta/models/gemini-2.5-pro:generateContent",
        provider=Provider.GEMINI,
        kind=EndpointKind.GEMINI_GENERATE_CONTENT,
    )


def _stream_generate_content_endpoint() -> EligibleEndpoint:
    return EligibleEndpoint(
        path="/gemini/v1beta/models/gemini-2.5-pro:streamGenerateContent",
        provider=Provider.GEMINI,
        kind=EndpointKind.GEMINI_STREAM_GENERATE_CONTENT,
    )


def _gemini_3_generate_content_endpoint() -> EligibleEndpoint:
    return EligibleEndpoint(
        path="/gemini/v1beta/models/gemini-3-pro-preview:generateContent",
        provider=Provider.GEMINI,
        kind=EndpointKind.GEMINI_GENERATE_CONTENT,
    )


def test_normalizes_idless_gemini_25_request_when_parts_tools_and_generation_config_are_present() -> None:
    # Given
    request = {
        "systemInstruction": {"parts": [{"text": "Be concise."}]},
        "contents": [
            {"role": "user", "parts": [{"text": "What is the weather in Paris?"}]},
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "name": "weather",
                            "args": {"city": "Paris"},
                        }
                    },
                    {"functionCall": {"name": "weather", "args": {"city": "London"}}},
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "weather",
                            "response": {"temperature_c": 23},
                        }
                    },
                    {"functionResponse": {"name": "weather", "response": {"temperature_c": 19}}},
                ],
            },
        ],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "weather",
                        "description": "Gets the current weather.",
                        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                    }
                ]
            }
        ],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO", "allowedFunctionNames": ["weather"]}},
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.9,
            "maxOutputTokens": 64,
            "stopSequences": ["END"],
            "candidateCount": 1,
        },
    }

    # When
    normalized = normalize_gemini_request(_generate_content_endpoint(), request, transaction_id="txn_gemini_request")
    payload = build_request_event_payload(normalized)

    # Then
    assert normalized.is_streaming is False
    assert normalized.final_model == "gemini-2.5-pro"
    assert payload["provider_request"] == request
    assert payload["final_request"] == {
        "model": "gemini-2.5-pro",
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": "Be concise."}]},
            {"role": "user", "content": [{"type": "text", "text": "What is the weather in Paris?"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "gemini:weather:0", "name": "weather", "input": {"city": "Paris"}},
                    {"type": "tool_use", "id": "gemini:weather:1", "name": "weather", "input": {"city": "London"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "gemini:weather:0",
                        "content": {"temperature_c": 23},
                    },
                    {"type": "tool_result", "tool_use_id": "gemini:weather:1", "content": {"temperature_c": 19}},
                ],
            },
        ],
        "tools": [
            {
                "name": "weather",
                "description": "Gets the current weather.",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ],
        "tool_choice": {"mode": "auto", "allowed_function_names": ["weather"]},
        "generation_config": {
            "temperature": 0.2,
            "topP": 0.9,
            "maxOutputTokens": 64,
            "stopSequences": ["END"],
            "candidateCount": 1,
        },
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 64,
        "stop": ["END"],
        "candidate_count": 1,
        "stream": False,
    }


def test_normalizes_idless_gemini_25_response_when_text_function_call_usage_and_safety_are_present() -> None:
    # Given
    response = {
        "responseId": "gemini-response-1",
        "modelVersion": "gemini-2.5-pro-001",
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "I found the weather."},
                        {
                            "functionCall": {
                                "name": "weather",
                                "args": {"city": "Paris"},
                            }
                        },
                    ],
                },
                "finishReason": "STOP",
                "safetyRatings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "NEGLIGIBLE"}],
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
            "cachedContentTokenCount": 2,
            "thoughtsTokenCount": 1,
        },
    }

    # When
    normalized = normalize_gemini_response(
        _generate_content_endpoint(),
        response,
        request_is_streaming=False,
        http_status=200,
        transaction_id="txn_gemini_response",
    )
    payload = build_response_event_payload(normalized)

    # Then
    assert normalized.is_streaming is False
    assert normalized.final_model == "gemini-2.5-pro-001"
    assert payload["provider_response"] == response
    assert payload["final_response"] == {
        "id": "gemini-response-1",
        "model": "gemini-2.5-pro-001",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I found the weather."},
            {"type": "tool_use", "id": "gemini:weather:0", "name": "weather", "input": {"city": "Paris"}},
        ],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "cache_read_input_tokens": 2,
            "reasoning_tokens": 1,
        },
        "safety_ratings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "NEGLIGIBLE"}],
    }


def test_normalizes_gemini_blocked_and_upstream_error_responses_without_synthetic_success() -> None:
    # Given
    blocked_response = {
        "responseId": "gemini-blocked",
        "modelVersion": "gemini-2.5-pro-001",
        "promptFeedback": {
            "blockReason": "SAFETY",
            "safetyRatings": [{"category": "HARM_CATEGORY_HATE_SPEECH", "blocked": True}],
        },
        "usageMetadata": {"promptTokenCount": 4, "totalTokenCount": 4},
    }
    error_response = {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}

    # When
    blocked = normalize_gemini_response(
        _generate_content_endpoint(),
        blocked_response,
        request_is_streaming=False,
        http_status=200,
        transaction_id="txn_gemini_blocked",
    )
    upstream_error = normalize_gemini_response(
        _generate_content_endpoint(),
        error_response,
        request_is_streaming=False,
        http_status=429,
        transaction_id="txn_gemini_error",
    )

    # Then
    assert build_response_event_payload(blocked)["final_response"] == {
        "id": "gemini-blocked",
        "model": "gemini-2.5-pro-001",
        "role": "assistant",
        "content": [],
        "stop_reason": "blocked",
        "usage": {"input_tokens": 4, "total_tokens": 4},
        "prompt_feedback": {
            "blockReason": "SAFETY",
            "safetyRatings": [{"category": "HARM_CATEGORY_HATE_SPEECH", "blocked": True}],
        },
    }
    assert build_response_event_payload(upstream_error)["final_response"] == {
        "role": "assistant",
        "content": [],
        "stop_reason": "error",
        "error": {"status_code": 429, "body": error_response},
    }


@pytest.mark.parametrize(
    ("finish_reason", "expected_stop_reason"),
    [("SAFETY", "safety"), ("RECITATION", "refusal")],
)
def test_normalizes_gemini_safety_and_refusal_finish_reasons_explicitly(
    finish_reason: str, expected_stop_reason: str
) -> None:
    # Given
    response = {
        "responseId": "gemini-finish",
        "modelVersion": "gemini-2.5-pro-001",
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "Cannot complete that."}]},
                "finishReason": finish_reason,
                "safetyRatings": [{"category": "HARM_CATEGORY_HARASSMENT", "blocked": True}],
            }
        ],
    }

    # When
    payload = build_response_event_payload(
        normalize_gemini_response(
            _generate_content_endpoint(),
            response,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_finish",
        )
    )

    # Then
    assert payload["final_response"]["stop_reason"] == expected_stop_reason
    assert payload["final_response"]["safety_ratings"] == [{"category": "HARM_CATEGORY_HARASSMENT", "blocked": True}]


def test_gemini_normalization_raises_typed_errors_for_malformed_and_unknown_eligible_variants() -> None:
    # Given
    missing_contents = {"generationConfig": {"maxOutputTokens": 8}}
    unknown_request_part = {"contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": "text/plain"}}]}]}
    unknown_response_finish = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "x"}]}, "finishReason": "NEW_REASON"}]
    }
    malformed_response_part = {
        "candidates": [{"content": {"role": "model", "parts": [{"functionResponse": {}}]}, "finishReason": "STOP"}]
    }

    # When / Then
    with pytest.raises(PassthroughNormalizeError) as missing_contents_error:
        normalize_gemini_request(_generate_content_endpoint(), missing_contents, transaction_id="txn_missing_contents")
    with pytest.raises(PassthroughNormalizeError) as unknown_request_error:
        normalize_gemini_request(
            _generate_content_endpoint(), unknown_request_part, transaction_id="txn_unknown_request"
        )
    with pytest.raises(PassthroughNormalizeError) as unknown_finish_error:
        normalize_gemini_response(
            _generate_content_endpoint(),
            unknown_response_finish,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_unknown_finish",
        )
    with pytest.raises(PassthroughNormalizeError) as malformed_part_error:
        normalize_gemini_response(
            _generate_content_endpoint(),
            malformed_response_part,
            request_is_streaming=False,
            http_status=200,
            transaction_id="txn_malformed_part",
        )

    assert (missing_contents_error.value.reason, missing_contents_error.value.detail) == (
        PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        "contents",
    )
    assert (unknown_request_error.value.reason, unknown_request_error.value.detail) == (
        PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        "contents",
    )
    assert (unknown_finish_error.value.reason, unknown_finish_error.value.detail) == (
        PassthroughNormalizeReason.UNSUPPORTED_VARIANT,
        "candidate.finishReason:NEW_REASON",
    )
    assert (malformed_part_error.value.reason, malformed_part_error.value.detail) == (
        PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        "candidate.content",
    )


def test_normalizes_safety_candidate_without_content_as_an_explicit_empty_assistant_turn() -> None:
    # Given
    response = {
        "responseId": "gemini-safety-no-content",
        "modelVersion": "gemini-2.5-pro-001",
        "candidates": [
            {
                "finishReason": "SAFETY",
                "safetyRatings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "blocked": True}],
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "totalTokenCount": 5},
    }

    # When
    normalized = normalize_gemini_response(
        _generate_content_endpoint(),
        response,
        request_is_streaming=False,
        http_status=200,
        transaction_id="txn_gemini_safety_no_content",
    )

    # Then
    assert build_response_event_payload(normalized)["final_response"] == {
        "id": "gemini-safety-no-content",
        "model": "gemini-2.5-pro-001",
        "role": "assistant",
        "content": [],
        "stop_reason": "safety",
        "usage": {"input_tokens": 5, "total_tokens": 5},
        "safety_ratings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "blocked": True}],
    }


def test_selects_the_zero_indexed_candidate_when_gemini_returns_multiple_candidates() -> None:
    # Given
    response = {
        "candidates": [
            {
                "index": 1,
                "content": {"role": "model", "parts": [{"text": "secondary candidate"}]},
                "finishReason": "STOP",
            },
            {
                "index": 0,
                "content": {"role": "model", "parts": [{"text": "canonical candidate"}]},
                "finishReason": "STOP",
            },
        ]
    }

    # When
    normalized = normalize_gemini_response(
        _generate_content_endpoint(),
        response,
        request_is_streaming=False,
        http_status=200,
        transaction_id="txn_gemini_candidate_zero",
    )

    # Then
    assert build_response_event_payload(normalized)["final_response"]["content"] == [
        {"type": "text", "text": "canonical candidate"}
    ]


def test_rejects_request_without_a_recoverable_function_response() -> None:
    # Given
    request = {
        "contents": [
            {
                "role": "user",
                "parts": [{"functionResponse": {"response": {"temperature_c": 23}}}],
            }
        ]
    }

    # When / Then
    with pytest.raises(PassthroughNormalizeError) as exc_info:
        normalize_gemini_request(_generate_content_endpoint(), request, transaction_id="txn_missing_response_name")

    assert (exc_info.value.reason, exc_info.value.detail) == (
        PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        "contents",
    )


def test_normalizes_json_array_stream_when_text_deltas_finish_and_usage_are_captured() -> None:
    # Given
    response = {
        "stream_format": "gemini-json-array",
        "chunks": [
            {
                "responseId": "gemini-json-stream",
                "modelVersion": "gemini-2.5-pro-001",
                "candidates": [{"index": 0, "content": {"role": "model", "parts": [{"text": "Hel"}]}}],
            },
            {
                "candidates": [
                    {"index": 0, "content": {"role": "model", "parts": [{"text": "lo"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
            },
        ],
        "final": None,
    }

    # When
    normalized = normalize_gemini_response(
        _stream_generate_content_endpoint(),
        response,
        request_is_streaming=True,
        http_status=200,
        transaction_id="txn_gemini_json_stream",
    )
    payload = build_response_event_payload(normalized)

    # Then
    assert payload["final_response"] == {
        "id": "gemini-json-stream",
        "model": "gemini-2.5-pro-001",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }


def test_normalizes_idless_gemini_25_sse_stream_when_function_call_deltas_are_merged_in_order() -> None:
    # Given
    response = {
        "stream_format": "gemini-sse",
        "chunks": [
            {
                "responseId": "gemini-sse-stream",
                "modelVersion": "gemini-2.5-pro-001",
                "candidates": [
                    {
                        "index": 0,
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "Checking weather. "},
                                {
                                    "functionCall": {
                                        "name": "weather",
                                        "args": {"city": "Paris"},
                                    }
                                },
                            ],
                        },
                    }
                ],
            },
            {
                "candidates": [
                    {
                        "index": 0,
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "weather",
                                        "args": {"units": "metric"},
                                    }
                                }
                            ],
                        },
                        "finishReason": "MAX_TOKENS",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 7},
            },
        ],
        "final": None,
    }

    # When
    normalized = normalize_gemini_response(
        _stream_generate_content_endpoint(),
        response,
        request_is_streaming=True,
        http_status=200,
        transaction_id="txn_gemini_sse_stream",
    )
    payload = build_response_event_payload(normalized)

    # Then
    assert payload["final_response"] == {
        "id": "gemini-sse-stream",
        "model": "gemini-2.5-pro-001",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Checking weather. "},
            {
                "type": "tool_use",
                "id": "gemini:weather:0",
                "name": "weather",
                "input": {"city": "Paris", "units": "metric"},
            },
        ],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
    }


def test_rejects_content_free_max_tokens_json_array_stream_before_canonical_output() -> None:
    # Given
    response = reassemble_gemini_json_array_stream(
        [b'[{"responseId":"gemini-empty-max-tokens","candidates":[{"index":0,"finishReason":"MAX_TOKENS"}]}]']
    )
    canonical_outputs: list[CanonicalResponsePayload] = []

    # When / Then
    with pytest.raises(PassthroughNormalizeError) as exc_info:
        canonical_outputs.append(
            build_response_event_payload(
                normalize_gemini_response(
                    _stream_generate_content_endpoint(),
                    response,
                    request_is_streaming=True,
                    http_status=200,
                    transaction_id="txn_gemini_empty_max_tokens_stream",
                )
            )
        )

    assert (exc_info.value.reason, exc_info.value.detail) == (
        PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        "candidate.content",
    )
    assert canonical_outputs == []


def test_rejects_content_free_max_tokens_buffered_response_before_canonical_output() -> None:
    # Given
    response = {
        "candidates": [
            {
                "content": {"role": "model", "parts": []},
                "finishReason": "MAX_TOKENS",
            }
        ]
    }
    canonical_outputs: list[CanonicalResponsePayload] = []

    # When / Then
    with pytest.raises(PassthroughNormalizeError) as exc_info:
        canonical_outputs.append(
            build_response_event_payload(
                normalize_gemini_response(
                    _generate_content_endpoint(),
                    response,
                    request_is_streaming=False,
                    http_status=200,
                    transaction_id="txn_gemini_empty_max_tokens_buffered",
                )
            )
        )

    assert (exc_info.value.reason, exc_info.value.detail) == (
        PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        "candidate.content",
    )
    assert canonical_outputs == []


@pytest.mark.parametrize(
    ("finish_reason", "expected_stop_reason"),
    [("SAFETY", "safety"), ("BLOCKLIST", "blocked")],
)
def test_normalizes_content_free_stream_when_finish_reason_allows_empty_turn(
    finish_reason: str, expected_stop_reason: str
) -> None:
    # Given
    response = reassemble_gemini_json_array_stream(
        [f'{{"candidates":[{{"index":0,"finishReason":"{finish_reason}"}}]}}'.encode()]
    )

    # When
    normalized = normalize_gemini_response(
        _stream_generate_content_endpoint(),
        response,
        request_is_streaming=True,
        http_status=200,
        transaction_id="txn_gemini_content_free_stream",
    )

    # Then
    assert build_response_event_payload(normalized)["final_response"] == {
        "role": "assistant",
        "content": [],
        "stop_reason": expected_stop_reason,
    }


def test_preserves_id_bearing_gemini_3_tool_pairing() -> None:
    # Given
    request = {
        "contents": [
            {"role": "model", "parts": [{"functionCall": {"id": "call_g3_weather", "name": "weather"}}]},
            {
                "role": "user",
                "parts": [{"functionResponse": {"id": "call_g3_weather", "name": "weather", "response": {"ok": True}}}],
            },
        ]
    }

    # When
    normalized = normalize_gemini_request(_gemini_3_generate_content_endpoint(), request)

    # Then
    assert build_request_event_payload(normalized)["final_request"]["messages"] == [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_g3_weather", "name": "weather", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_g3_weather", "content": {"ok": True}}],
        },
    ]


@pytest.mark.parametrize(
    ("response", "reason", "detail"),
    [
        (
            {"stream_format": "gemini-json-array", "chunks": [], "final": None, "capture_truncated": True},
            PassthroughNormalizeReason.CAPTURE_TRUNCATED,
            "stream capture truncated",
        ),
        (
            {"stream_format": "gemini-sse", "chunks": [], "final": None, "raw": "data: {broken"},
            PassthroughNormalizeReason.CAPTURE_TRUNCATED,
            "stream capture truncated",
        ),
        (
            {"stream_format": "openai-sse", "chunks": [], "final": None},
            PassthroughNormalizeReason.UNSUPPORTED_VARIANT,
            "stream_format:openai-sse",
        ),
        (
            {"stream_format": "gemini-sse", "chunks": [{"event": "unknown"}], "final": None},
            PassthroughNormalizeReason.UNSUPPORTED_VARIANT,
            "stream chunk",
        ),
        (
            {
                "stream_format": "gemini-sse",
                "chunks": [
                    {
                        "candidates": [
                            {"content": {"role": "model", "parts": [{"inlineData": {"mimeType": "text/plain"}}]}}
                        ]
                    }
                ],
                "final": None,
            },
            PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
            "candidate.finishReason",
        ),
        (
            {
                "stream_format": "gemini-json-array",
                "chunks": [{"candidates": [{"content": {"role": "model", "parts": [{"text": "partial"}]}}]}],
                "final": None,
            },
            PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
            "candidate.finishReason",
        ),
    ],
)
def test_stream_normalization_raises_typed_errors_when_wrapper_or_chunk_is_incomplete(
    response: JsonObject, reason: PassthroughNormalizeReason, detail: str
) -> None:
    # Given the stored wrapper or a provider chunk is incomplete or unsupported

    # When / Then
    with pytest.raises(PassthroughNormalizeError) as exc_info:
        normalize_gemini_response(
            _stream_generate_content_endpoint(),
            response,
            request_is_streaming=True,
            http_status=200,
            transaction_id="txn_gemini_bad_stream",
        )

    assert (exc_info.value.reason, exc_info.value.detail) == (reason, detail)


def test_stream_normalization_falls_back_to_raw_when_chunk_has_unmodelled_field() -> None:
    # Given: a stream chunk carrying a field the google-genai SDK model doesn't
    # know about yet (usageMetadata.serviceTier is the concrete case observed in
    # prod). The buffered path (normalize_gemini_response) falls back to the raw
    # payload on ValidationError; the stream path must mirror that or streamed
    # and buffered captures of the same conversation get opposite outcomes.
    response = {
        "stream_format": "gemini-sse",
        "chunks": [
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "hi"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 1,
                    "totalTokenCount": 2,
                    "serviceTier": "standard",
                },
            },
        ],
        "final": None,
    }

    # When
    normalized = normalize_gemini_response(
        _stream_generate_content_endpoint(),
        response,
        request_is_streaming=True,
        http_status=200,
        transaction_id="txn_gemini_stream_service_tier",
    )

    # Then: the chunk normalizes cleanly (no MALFORMED_PAYLOAD), and usage from the
    # unmodelled-field-carrying chunk is preserved.
    payload = build_response_event_payload(normalized)
    assert payload["final_response"]["usage"]["input_tokens"] == 1
    assert payload["final_response"]["usage"]["output_tokens"] == 1


def test_normalizes_gemini_request_when_content_role_is_omitted_defaults_to_user() -> None:
    # Given: Gemini API spec makes `role` OPTIONAL in contents[] (defaults to "user").
    # A minimal probe like {"contents": [{"parts": [{"text": "..."}]}]} is a valid Gemini call
    # (observed in prod as a health-check probe against gemini-2.5-flash) and must materialize.
    request = {"contents": [{"parts": [{"text": "say prod-capture-ok"}]}]}

    # When
    normalized = normalize_gemini_request(_generate_content_endpoint(), request, transaction_id="txn_gemini_no_role")
    payload = build_request_event_payload(normalized)

    # Then: the message role defaults to "user" instead of failing MISSING_REQUIRED_FIELD:contents.
    assert payload["final_request"]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "say prod-capture-ok"}]},
    ]
