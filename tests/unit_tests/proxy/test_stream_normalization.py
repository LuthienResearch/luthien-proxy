"""Unit tests for stream normalization utilities."""

from __future__ import annotations

import json

from luthien_proxy.proxy.stream_normalization import (
    anthropic_stream_to_openai,
    openai_chunks_to_anthropic,
)

CLAUDE_TEXT_EVENTS = [
    """event: message_start
data: {"type":"message_start","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_01Syufym9DZKGgeEtT3BDEAF","type":"message","role":"assistant","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":4,"cache_creation_input_tokens":15463,"cache_read_input_tokens":5432,"cache_creation":{"ephemeral_5m_input_tokens":15463,"ephemeral_1h_input_tokens":0},"output_tokens":1,"service_tier":"standard"}}}
""",
    """event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}
""",
    """event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hey"}}
""",
    """event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" Jai! How's it going? What"}}
""",
    """event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" can I help you with today?"}}
""",
    """event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"input_tokens":4,"cache_creation_input_tokens":15463,"cache_read_input_tokens":5432,"output_tokens":3}}
""",
    """event: message_stop
data: {"type":"message_stop"}
""",
]

CLAUDE_TOOL_EVENTS = [
    """event: message_start
data: {"type":"message_start","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_tool","type":"message","role":"assistant","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":4,"cache_creation_input_tokens":47,"cache_read_input_tokens":20895,"cache_creation":{"ephemeral_5m_input_tokens":47,"ephemeral_1h_input_tokens":0},"output_tokens":1,"service_tier":"standard"}}}
""",
    """event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_013mJywkik3Tcf58fvmaYxfw","name":"Write","input":{}}}
""",
    """event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\": \\"/Users/jaidhyani/Desktop/luthien-proxy/python_for_cpp_devs.py\\""}}
""",
    """event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":", \\"content\\": \\"#!/usr/bin/env python3"}}
""",
    """event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\\"}"}}
""",
    """event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"input_tokens":4,"cache_creation_input_tokens":47,"cache_read_input_tokens":20895,"output_tokens":10}}
""",
    """event: message_stop
data: {"type":"message_stop"}
""",
]

EXPECTED_TEXT = "Hey Jai! How's it going? What can I help you with today?"
EXPECTED_TOOL_ARGS = (
    '{"file_path": "/Users/jaidhyani/Desktop/luthien-proxy/python_for_cpp_devs.py", '
    '"content": "#!/usr/bin/env python3"}'
)


def _aggregate_text(chunks: list[dict]) -> str:
    return "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks if chunk["choices"][0]["delta"])


def _aggregate_tool_arguments(chunks: list[dict]) -> str:
    buffer: list[str] = []
    for chunk in chunks:
        tool_calls = chunk["choices"][0]["delta"].get("tool_calls") or []
        for tool_call in tool_calls:
            buffer.append(tool_call["function"]["arguments"])
    return "".join(buffer)


def _normalize_events(events: list[str]) -> list[tuple]:
    normalized: list[tuple] = []
    for payload in events:
        segments = [seg for seg in payload.split("\n\n") if seg.strip()]
        for segment in segments:
            event_type = None
            data: dict | None = None
            for line in segment.splitlines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
            if event_type is None or data is None:
                continue
            if event_type == "message_start":
                message = data["message"]
                normalized.append(("message_start", message.get("model"), message.get("role")))
            elif event_type == "content_block_start":
                normalized.append(("content_block_start", data.get("index"), data.get("content_block", {}).get("type")))
            elif event_type == "content_block_delta":
                delta = data.get("delta", {})
                normalized.append(
                    (
                        "content_block_delta",
                        data.get("index"),
                        delta.get("type"),
                        delta.get("text") or delta.get("partial_json", ""),
                    )
                )
            elif event_type == "content_block_stop":
                normalized.append(("content_block_stop", data.get("index")))
            elif event_type == "message_delta":
                normalized.append(("message_delta", data.get("delta", {}).get("stop_reason")))
            elif event_type == "message_stop":
                normalized.append(("message_stop", None))
    return normalized


def _encode_events(events: list[str]) -> list[bytes]:
    """Convert textual fixtures into byte payloads."""
    return [payload.encode("utf-8") for payload in events]


def test_anthropic_stream_to_openai_text_sample() -> None:
    chunks = anthropic_stream_to_openai(_encode_events(CLAUDE_TEXT_EVENTS))
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert _aggregate_text(chunks) == EXPECTED_TEXT
    finish_chunks = [chunk for chunk in chunks if chunk["choices"][0]["finish_reason"]]
    assert finish_chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_anthropic_stream_to_openai_tool_sample() -> None:
    chunks = anthropic_stream_to_openai(_encode_events(CLAUDE_TOOL_EVENTS))
    tool_args = _aggregate_tool_arguments(chunks)
    assert tool_args == EXPECTED_TOOL_ARGS
    finish_chunks = [chunk for chunk in chunks if chunk["choices"][0]["finish_reason"]]
    assert finish_chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_round_trip_anthropic_openai_preserves_structure() -> None:
    source_events = CLAUDE_TEXT_EVENTS + CLAUDE_TOOL_EVENTS
    chunks = anthropic_stream_to_openai(_encode_events(source_events))
    round_trip_events = openai_chunks_to_anthropic(chunks)
    expected = _normalize_events(source_events)
    actual = _normalize_events(round_trip_events)
    assert actual == expected


def test_round_trip_openai_anthropic_preserves_content() -> None:
    source_events = CLAUDE_TEXT_EVENTS + CLAUDE_TOOL_EVENTS
    chunks = anthropic_stream_to_openai(_encode_events(source_events))
    recreated_chunks = anthropic_stream_to_openai(_encode_events(openai_chunks_to_anthropic(chunks)))
    assert _aggregate_text(recreated_chunks) == EXPECTED_TEXT
    assert _aggregate_tool_arguments(recreated_chunks) == EXPECTED_TOOL_ARGS
