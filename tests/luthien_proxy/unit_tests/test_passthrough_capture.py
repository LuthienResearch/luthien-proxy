from __future__ import annotations

from luthien_proxy.passthrough_capture import (
    build_passthrough_headers,
    build_upstream_url,
    client_response_headers,
    parse_gemini_model,
    parse_openai_model,
    reassemble_gemini_json_array_stream,
    reassemble_gemini_sse_stream,
    reassemble_openai_sse_stream,
)
from luthien_proxy.request_log.sanitize import sanitize_headers, sanitize_url


def test_build_passthrough_headers_forwards_client_keys_and_strips_internal_headers() -> None:
    # Given
    headers = {
        "Authorization": "Bearer client-openai-key",
        "x-goog-api-key": "client-google-key",
        "x-luthien-model": "gpt-4.1",
        "Connection": "keep-alive",
        "Host": "proxy.local",
        "Content-Type": "application/json",
    }

    # When
    forwarded = build_passthrough_headers(headers.items())

    # Then
    assert forwarded == {
        "Authorization": "Bearer client-openai-key",
        "x-goog-api-key": "client-google-key",
        "Content-Type": "application/json",
    }


def test_build_upstream_url_preserves_the_client_query_string() -> None:
    # Given
    base_url = "https://api.openai.com/"

    # When
    upstream_url = build_upstream_url(base_url, "v1/chat/completions", "stream=true")

    # Then
    assert upstream_url == "https://api.openai.com/v1/chat/completions?stream=true"


def test_sanitize_redacts_google_api_key_header_and_key_query_param() -> None:
    # Given
    headers = {"x-goog-api-key": "google-secret", "x-trace-id": "trace-123"}
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent?key=google-secret&alt=sse"

    # When
    sanitized_headers = sanitize_headers(headers)
    sanitized_url = sanitize_url(url)

    # Then
    assert sanitized_headers == {"x-goog-api-key": "[REDACTED]", "x-trace-id": "trace-123"}
    assert sanitized_url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent?key=%5BREDACTED%5D&alt=sse"
    )
    assert "google-secret" not in sanitized_url


def test_model_parsing_uses_body_for_openai_and_url_for_gemini() -> None:
    # Given
    openai_body = {"model": "gpt-4.1", "input": "hello"}
    gemini_path = "v1beta/models/gemini-2.5-pro:generateContent"

    # When / Then
    assert parse_openai_model(openai_body, override=None) == "gpt-4.1"
    assert parse_openai_model(openai_body, override="gpt-4.1-mini") == "gpt-4.1-mini"
    assert parse_gemini_model(gemini_path, {"model": "fallback"}, override=None) == "gemini-2.5-pro"
    assert parse_gemini_model("v1beta/generate", {"model": "fallback"}, override=None) == "fallback"


def test_streaming_reassembly_returns_documented_wrappers() -> None:
    # Given
    openai_chunks = [
        b'data: {"id":"evt-1","output_text":"hel"}\n\n',
        b'data: {"id":"evt-2","output_text":"hello"}\n\n',
        b"data: [DONE]\n\n",
    ]
    gemini_json_chunks = [b'[{"text":"hel"},', b'{"text":"hello"}]']
    gemini_sse_chunks = [b'data: {"text":"hel"}\n\n', b'data: {"text":"hello"}\n\n']

    # When / Then
    assert reassemble_openai_sse_stream(openai_chunks) == {
        "stream_format": "openai-sse",
        "events": [
            {"id": "evt-1", "output_text": "hel"},
            {"id": "evt-2", "output_text": "hello"},
        ],
        "final": {"id": "evt-2", "output_text": "hello"},
    }
    assert reassemble_gemini_json_array_stream(gemini_json_chunks) == {
        "stream_format": "gemini-json-array",
        "chunks": [{"text": "hel"}, {"text": "hello"}],
        "final": None,
    }
    assert reassemble_gemini_sse_stream(gemini_sse_chunks) == {
        "stream_format": "gemini-sse",
        "chunks": [{"text": "hel"}, {"text": "hello"}],
        "final": None,
    }


def test_client_response_headers_strip_encoding_length_and_hop_by_hop_headers() -> None:
    # Given
    upstream_headers = {
        "content-encoding": "gzip",
        "Content-Length": "123",
        "Transfer-Encoding": "chunked",
        "Connection": "keep-alive",
        "content-type": "application/json",
        "x-request-id": "req-123",
    }

    # When
    returned_headers = client_response_headers(upstream_headers)

    # Then
    assert returned_headers == {"content-type": "application/json", "x-request-id": "req-123"}


def test_streaming_reassembly_preserves_malformed_chunks_with_raw_fallback() -> None:
    # Given
    openai_chunks = [b"data: {not json}\n\n", b'data: {"ok": true}\n\n']
    gemini_array_chunks = [b""]

    # When / Then
    assert reassemble_openai_sse_stream(openai_chunks) == {
        "stream_format": "openai-sse",
        "events": [{"ok": True}],
        "raw": 'data: {not json}\n\ndata: {"ok": true}\n\n',
        "final": {"ok": True},
    }
    assert reassemble_gemini_json_array_stream(gemini_array_chunks) == {
        "stream_format": "gemini-json-array",
        "chunks": [],
        "raw": "",
        "final": None,
    }
