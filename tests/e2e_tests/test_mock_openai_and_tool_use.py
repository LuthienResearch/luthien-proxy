"""Mock e2e tests for tool use responses.

Covers MockToolResponse producing Anthropic tool_use content blocks
(both non-streaming JSON and SSE streaming with input_json_delta events).

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_openai_and_tool_use.py -v
"""

import json

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL
from tests.e2e_tests.mock_anthropic.responses import tool_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def parse_anthropic_sse_stream(lines: list[str]) -> list[tuple[str, dict]]:
    """Parse Anthropic SSE format: 'event: type\\ndata: {json}' pairs.

    Returns list of (event_type, parsed_data) tuples.
    """
    events = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("event: "):
            event_type = lines[i][7:].strip()
            if i + 1 < len(lines) and lines[i + 1].startswith("data: "):
                try:
                    parsed = json.loads(lines[i + 1][6:].strip())
                    events.append((event_type, parsed))
                except json.JSONDecodeError:
                    pass
                i += 2
                continue
        i += 1
    return events


# =============================================================================
# Section 1: Tool use tests (Anthropic /v1/messages endpoint)
# =============================================================================


@pytest.mark.asyncio
async def test_tool_use_non_streaming_response_structure(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Non-streaming tool_use response has correct structure and stop_reason."""
    mock_anthropic.enqueue(tool_response("get_weather", {"location": "London", "unit": "celsius"}))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "What's the weather in London?"}],
                "max_tokens": 100,
                "stream": False,
                "tools": [
                    {
                        "name": "get_weather",
                        "description": "Get weather",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string"},
                                "unit": {"type": "string"},
                            },
                            "required": ["location"],
                        },
                    }
                ],
            },
            headers=_HEADERS,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["stop_reason"] == "tool_use"
    assert len(data["content"]) >= 1

    tool_block = next(b for b in data["content"] if b["type"] == "tool_use")
    assert tool_block["name"] == "get_weather"
    assert tool_block["input"]["location"] == "London"
    assert tool_block["id"].startswith("toolu_")


@pytest.mark.asyncio
async def test_tool_use_streaming_event_sequence(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Streaming tool_use emits the full required SSE event sequence with correct structure."""
    mock_anthropic.enqueue(tool_response("search", {"query": "test query"}))

    lines: list[str] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "search for something"}],
                "max_tokens": 100,
                "stream": True,
                "tools": [
                    {
                        "name": "search",
                        "description": "Search the web",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ],
            },
            headers=_HEADERS,
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                lines.append(line)

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

    # content_block_start must have type=tool_use and name=search
    content_block_start_data = next((d for et, d in events if et == "content_block_start"), None)
    assert content_block_start_data is not None
    cb = content_block_start_data["content_block"]
    assert cb["type"] == "tool_use"
    assert cb["name"] == "search"

    # All content_block_delta events must have type=input_json_delta
    delta_events = [(et, d) for et, d in events if et == "content_block_delta"]
    assert len(delta_events) >= 1, "Should have at least one content_block_delta"
    for _, delta_data in delta_events:
        assert delta_data["delta"]["type"] == "input_json_delta"

    # Accumulated partial_json must parse as JSON with query == "test query"
    accumulated = "".join(d["delta"]["partial_json"] for _, d in delta_events)
    result = json.loads(accumulated)
    assert result["query"] == "test query"

    # message_delta must carry stop_reason=tool_use
    message_delta_data = next((d for et, d in events if et == "message_delta"), None)
    assert message_delta_data is not None
    assert message_delta_data["delta"]["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_tool_use_stop_reason_is_tool_use(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Non-streaming tool_use response always reports stop_reason='tool_use'."""
    mock_anthropic.enqueue(tool_response("calculate", {"expression": "2+2"}))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "calculate 2+2"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers=_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["stop_reason"] == "tool_use"
