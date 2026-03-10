"""Mock e2e tests for Anthropic SSE streaming structure validation.

Unlike the real streaming structure tests, these use the mock Anthropic server
so assertions are deterministic — the mock emits exactly the events we configure,
and we verify the gateway preserves (or transforms) them correctly.

Covers:
- Complete event lifecycle (message_start → content_block_* → message_delta → message_stop)
- message_start payload structure (id, role, model, usage)
- content_block_start / content_block_delta / content_block_stop structure and indices
- message_delta with stop_reason
- SSE wire format (event: / data: pairs)

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_streaming_structure.py -v
"""

import json

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL
from tests.e2e_tests.mock_anthropic.responses import stream_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": True,
}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


# === SSE parse helper (same as in test_streaming_chunk_structure.py) ===


def parse_anthropic_sse_stream(lines: list[str]) -> list[tuple[str, dict]]:
    """Parse Anthropic SSE format: 'event: type\\ndata: {json}' pairs.

    Returns list of (event_type, parsed_data) tuples.
    """
    events = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("event: "):
            event_type = line[7:].strip()
            if i + 1 < len(lines) and lines[i + 1].startswith("data: "):
                data = lines[i + 1][6:].strip()
                try:
                    parsed = json.loads(data)
                    events.append((event_type, parsed))
                except json.JSONDecodeError:
                    pass
                i += 2
                continue
        i += 1
    return events


async def collect_sse_lines(url: str, body: dict, headers: dict) -> list[str]:
    """Stream a request and return all SSE lines."""
    lines = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            assert response.status_code == 200, f"Unexpected status: {response.status_code}"
            assert "text/event-stream" in response.headers.get("content-type", "")
            async for line in response.aiter_lines():
                lines.append(line)
    return lines


# =============================================================================
# Event lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_anthropic_streaming_event_lifecycle(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway emits all required Anthropic events in the correct order."""
    mock_anthropic.enqueue(stream_response("hello world"))

    lines = await collect_sse_lines(f"{GATEWAY_URL}/v1/messages", _REQUEST, _HEADERS)
    events = parse_anthropic_sse_stream(lines)
    event_types = [et for et, _ in events]

    required = {
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    }
    missing = required - set(event_types)
    assert not missing, f"Missing SSE events: {missing}"

    # Ordering invariants
    assert event_types[0] == "message_start", "First event must be message_start"
    assert event_types[-1] == "message_stop", "Last event must be message_stop"

    start_idx = event_types.index("content_block_start")
    delta_idx = event_types.index("content_block_delta")
    assert start_idx < delta_idx, "content_block_start must precede content_block_delta"


# =============================================================================
# message_start structure
# =============================================================================


@pytest.mark.asyncio
async def test_anthropic_streaming_message_start_structure(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """message_start event carries required message metadata."""
    mock_anthropic.enqueue(stream_response("hi"))

    lines = await collect_sse_lines(f"{GATEWAY_URL}/v1/messages", _REQUEST, _HEADERS)
    events = parse_anthropic_sse_stream(lines)

    message_start_data = next((data for et, data in events if et == "message_start"), None)
    assert message_start_data is not None, "message_start event not found"

    assert message_start_data["type"] == "message_start"
    msg = message_start_data["message"]

    assert "id" in msg, "message.id must be present"
    assert msg["id"].startswith("msg_"), f"message.id should start with 'msg_', got: {msg['id']}"
    assert msg.get("type") == "message"
    assert msg.get("role") == "assistant"
    assert isinstance(msg.get("content"), list)
    assert "usage" in msg, "message_start must carry usage"
    assert "input_tokens" in msg["usage"]


# =============================================================================
# content_block structure and indices
# =============================================================================


@pytest.mark.asyncio
async def test_anthropic_streaming_content_block_structure(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """content_block_start, content_block_delta and content_block_stop are well-formed."""
    mock_anthropic.enqueue(stream_response("abc def ghi", chunks=["abc ", "def ", "ghi"]))

    lines = await collect_sse_lines(f"{GATEWAY_URL}/v1/messages", _REQUEST, _HEADERS)
    events = parse_anthropic_sse_stream(lines)

    # content_block_start
    starts = [(et, d) for et, d in events if et == "content_block_start"]
    assert len(starts) == 1, "Should have exactly one content_block_start"
    _, start_data = starts[0]
    assert start_data["type"] == "content_block_start"
    assert "index" in start_data
    assert start_data["index"] == 0
    cb = start_data["content_block"]
    assert cb["type"] == "text"

    # content_block_delta — three chunks
    deltas = [(et, d) for et, d in events if et == "content_block_delta"]
    assert len(deltas) == 3, f"Expected 3 deltas (one per chunk), got {len(deltas)}"
    for _, delta_data in deltas:
        assert delta_data["type"] == "content_block_delta"
        assert delta_data["index"] == 0
        assert delta_data["delta"]["type"] == "text_delta"
        assert "text" in delta_data["delta"]

    collected = "".join(d["delta"]["text"] for _, d in deltas)
    assert collected == "abc def ghi"

    # content_block_stop
    stops = [(et, d) for et, d in events if et == "content_block_stop"]
    assert len(stops) == 1
    _, stop_data = stops[0]
    assert stop_data["type"] == "content_block_stop"
    assert stop_data["index"] == 0


# =============================================================================
# message_delta with stop_reason
# =============================================================================


@pytest.mark.asyncio
async def test_anthropic_streaming_message_delta_stop_reason(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """message_delta carries stop_reason; message_stop terminates the stream."""
    mock_anthropic.enqueue(stream_response("done"))

    lines = await collect_sse_lines(f"{GATEWAY_URL}/v1/messages", _REQUEST, _HEADERS)
    events = parse_anthropic_sse_stream(lines)

    message_deltas = [(et, d) for et, d in events if et == "message_delta"]
    assert len(message_deltas) >= 1, "Should have at least one message_delta"

    last_delta_data = message_deltas[-1][1]
    assert last_delta_data["type"] == "message_delta"
    assert "delta" in last_delta_data
    stop_reason = last_delta_data["delta"].get("stop_reason")
    assert stop_reason is not None, "message_delta must include stop_reason"
    assert stop_reason in {"end_turn", "max_tokens", "stop_sequence", "tool_use"}, (
        f"Unexpected stop_reason: {stop_reason}"
    )

    # message_stop is the final event
    message_stops = [(et, d) for et, d in events if et == "message_stop"]
    assert len(message_stops) == 1
    assert message_stops[0][1]["type"] == "message_stop"


# =============================================================================
# SSE wire format compliance
# =============================================================================


@pytest.mark.asyncio
async def test_anthropic_streaming_sse_wire_format(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Every 'event:' line is followed by a 'data:' line (Anthropic SSE wire format)."""
    mock_anthropic.enqueue(stream_response("format check"))

    lines = await collect_sse_lines(f"{GATEWAY_URL}/v1/messages", _REQUEST, _HEADERS)

    event_lines = [line for line in lines if line.startswith("event: ")]
    assert len(event_lines) > 0, "Should have event: lines"

    i = 0
    while i < len(lines):
        if lines[i].startswith("event: "):
            # Find next non-empty line — it must be a data line
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            assert j < len(lines), f"event at line {i} has no following data line"
            assert lines[j].startswith("data: "), f"Line after 'event: ...' must be 'data: ...', got: {lines[j]!r}"
        i += 1
