"""Unit tests for stream normalization utilities."""

from __future__ import annotations

import json
import time

from luthien_proxy.proxy.stream_normalization import (
    AnthropicToOpenAIAdapter,
    OpenAIToAnthropicAdapter,
)


def _sse(event: str, data: dict) -> str:
    """Helper to build SSE payloads."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def test_anthropic_stream_to_openai_converts_text_chunks() -> None:
    adapter = AnthropicToOpenAIAdapter()
    events = [
        _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test",
                    "model": "claude-3",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {},
                },
            },
        ),
        _sse(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        _sse(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
        ),
        _sse(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}},
        ),
    ]

    chunks = []
    for payload in events:
        chunks.extend(adapter.process(payload))

    assert chunks, "Expected at least one chunk to be produced"
    first = chunks[0]["choices"][0]["delta"]
    assert first["role"] == "assistant"
    text_chunk = [chunk for chunk in chunks if chunk["choices"][0]["delta"].get("content")]
    assert text_chunk, "Expected a text delta chunk"
    assert text_chunk[0]["choices"][0]["delta"]["content"] == "Hello"
    finish_chunk = [chunk for chunk in chunks if chunk["choices"][0]["finish_reason"]]
    assert finish_chunk[0]["choices"][0]["finish_reason"] == "stop"


def test_anthropic_stream_to_openai_converts_tool_calls() -> None:
    adapter = AnthropicToOpenAIAdapter()
    events = [
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "tool_1", "name": "Write", "input": {}},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"'},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": 'name": "demo"}'},
            },
        ),
    ]

    chunks = []
    for payload in events:
        chunks.extend(adapter.process(payload))

    tool_chunks = [chunk for chunk in chunks if chunk["choices"][0]["delta"].get("tool_calls")]
    arguments = [chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] for chunk in tool_chunks]
    assert arguments == ["", '{"', 'name": "demo"}']


def test_openai_chunks_to_anthropic_round_trip() -> None:
    adapter = OpenAIToAnthropicAdapter(model="claude-3", message_id="msg_test")
    first_chunk = {
        "id": "chatcmpl-test",
        "model": "gpt-5",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "Hello"},
                "finish_reason": None,
                "logprobs": None,
            }
        ],
    }

    events = adapter.process(first_chunk)
    events += adapter.process(
        {
            "id": "chatcmpl-test",
            "model": "gpt-5",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": " world"},
                    "finish_reason": None,
                    "logprobs": None,
                }
            ],
        }
    )
    events += adapter.process(
        {
            "id": "chatcmpl-test",
            "model": "gpt-5",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": " world"},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
        }
    )
    events += adapter.finalize()

    assert any("message_start" in evt for evt in events)
    assert any('"text_delta"' in evt and "Hello" in evt for evt in events)
    assert any('"stop_reason":"end_turn"' in evt for evt in events)
