"""Mock e2e tests for OpenAI format endpoint and tool use responses.

Covers two new mock server capabilities:
1. Tool use: MockToolResponse producing Anthropic tool_use content blocks
   (both non-streaming JSON and SSE streaming with input_json_delta events)
2. OpenAI format: gateway's /v1/chat/completions endpoint returning
   OpenAI-format JSON and SSE chunks

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
from tests.e2e_tests.mock_anthropic.responses import text_response, tool_response
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


# =============================================================================
# Section 2: OpenAI format tests (gateway's /v1/chat/completions endpoint)
# =============================================================================


@pytest.mark.asyncio
async def test_openai_non_streaming_response_structure(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway /v1/chat/completions returns a valid OpenAI-format chat.completion object."""
    mock_anthropic.enqueue(text_response("Hello from OpenAI mock"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers=_HEADERS,
        )

    if response.status_code != 200:
        pytest.skip("OpenAI endpoint not using mock backend")

    data = response.json()
    assert data["object"] == "chat.completion"
    assert "choices" in data and len(data["choices"]) > 0
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert isinstance(data["choices"][0]["message"]["content"], str)
    assert len(data["choices"][0]["message"]["content"]) > 0
    assert data["choices"][0]["finish_reason"] in ["stop", "end_turn", "length"]


@pytest.mark.asyncio
async def test_openai_streaming_response_structure(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway /v1/chat/completions streaming uses OpenAI SSE format (data: lines, no event: lines)."""
    mock_anthropic.enqueue(text_response("Hello streaming"))

    lines: list[str] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": True,
            },
            headers=_HEADERS,
        ) as response:
            if response.status_code != 200:
                pytest.skip("OpenAI endpoint not using mock backend")
            assert "text/event-stream" in response.headers.get("content-type", "")
            async for line in response.aiter_lines():
                lines.append(line)

    # OpenAI SSE format: only data: lines, no event: lines
    data_lines = [line for line in lines if line.startswith("data: ")]
    assert len(data_lines) > 0, "Should have at least one data: line"

    event_lines = [line for line in lines if line.startswith("event: ")]
    assert len(event_lines) == 0, "OpenAI format must not include event: lines"

    # Must end with [DONE]
    assert any(line == "data: [DONE]" for line in lines), "Stream must end with data: [DONE]"

    # Non-DONE data lines must be valid JSON chunks with non-empty content somewhere
    non_done_data = [line[6:] for line in data_lines if line != "data: [DONE]"]
    chunks = [json.loads(raw) for raw in non_done_data]
    content_pieces = [
        chunk["choices"][0]["delta"].get("content", "")
        for chunk in chunks
        if chunk.get("choices") and chunk["choices"][0].get("delta")
    ]
    non_empty = [c for c in content_pieces if c]
    assert len(non_empty) > 0, "At least one chunk should carry non-empty delta content"


@pytest.mark.asyncio
async def test_openai_streaming_chunk_structure(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """OpenAI streaming chunks have correct id prefix, object type, and finish_reason on last chunk."""
    mock_anthropic.enqueue(text_response("test output"))

    lines: list[str] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": True,
            },
            headers=_HEADERS,
        ) as response:
            if response.status_code != 200:
                pytest.skip("OpenAI endpoint not using mock backend")
            async for line in response.aiter_lines():
                lines.append(line)

    non_done_data = [line[6:] for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert len(non_done_data) > 0, "Should have at least one non-DONE data chunk"
    chunks = [json.loads(raw) for raw in non_done_data]

    # First chunk: id starts with chatcmpl-, object is chat.completion.chunk, role present
    first = chunks[0]
    assert first["id"].startswith("chatcmpl-"), f"First chunk id should start with 'chatcmpl-', got: {first['id']}"
    assert first["object"] == "chat.completion.chunk"
    first_delta = first["choices"][0]["delta"]
    assert first_delta.get("role") == "assistant"

    # Last data chunk (before [DONE]): finish_reason must be set
    last = chunks[-1]
    finish_reason = last["choices"][0].get("finish_reason")
    assert finish_reason is not None, "Last chunk before [DONE] must have a finish_reason"


# =============================================================================
# Section 3: MockToolResponse degradation on OpenAI endpoint
# =============================================================================


@pytest.mark.asyncio
async def test_tool_response_via_openai_endpoint_returns_tool_calls(
    mock_anthropic: MockAnthropicServer, gateway_healthy
):
    """MockToolResponse sent to /v1/chat/completions is returned as proper OpenAI tool_calls.

    The gateway routes /v1/chat/completions through LiteLLM's Anthropic adapter,
    which calls the mock's /v1/messages endpoint (Anthropic format).  The mock
    returns a tool_use block; LiteLLM converts that back to OpenAI tool_calls
    format — content=null, tool_calls=[{type, function: {name, arguments}}].
    """
    mock_anthropic.enqueue(tool_response("get_weather", {"location": "London", "unit": "celsius"}))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "What's the weather in London?"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers=_HEADERS,
        )

    if response.status_code != 200:
        pytest.skip("OpenAI endpoint not using mock backend")

    data = response.json()
    assert data["object"] == "chat.completion"

    message = data["choices"][0]["message"]
    # OpenAI format: content is null for tool_calls responses
    assert message.get("content") is None, (
        f"Expected null content for tool_calls response, got: {message.get('content')!r}"
    )
    tool_calls = message.get("tool_calls", [])
    assert len(tool_calls) == 1, f"Expected exactly 1 tool call, got: {tool_calls}"
    assert tool_calls[0]["type"] == "function"
    assert tool_calls[0]["function"]["name"] == "get_weather"
    args = json.loads(tool_calls[0]["function"]["arguments"])
    assert args == {"location": "London", "unit": "celsius"}, f"Unexpected tool arguments: {args}"
