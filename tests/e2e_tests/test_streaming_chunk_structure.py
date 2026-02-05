# ABOUTME: E2E tests validating streaming chunk structure and SSE format
# ABOUTME: Tests proper event sequences, field presence, and ordering for OpenAI and Anthropic

"""E2E tests for streaming chunk structure validation.

These tests verify that the gateway produces properly structured streaming responses:
- Correct SSE format (event types, data fields, delimiters)
- Proper event sequences (start → content → stop)
- Required fields in each chunk type
- Correct ordering and indices

Based on reference data in _scratch/ showing actual API responses.
"""

import asyncio
import json
import os

import httpx
import pytest

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest.fixture
def admin_headers():
    """Provide admin authentication headers."""
    return {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.fixture(scope="module")
async def noop_policy_active():
    """Ensure NoOpPolicy is active for streaming structure tests.

    These tests validate that the gateway preserves the original streaming
    format from upstream providers. NoOpPolicy is required because it passes
    chunks through unchanged, preserving original IDs and structure.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        admin_headers = {"Authorization": f"Bearer {ADMIN_API_KEY}"}

        # Set NoOp policy using the /admin/policy/set endpoint
        set_response = await client.post(
            f"{GATEWAY_URL}/admin/policy/set",
            headers=admin_headers,
            json={
                "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
                "config": {},
                "enabled_by": "e2e-streaming-tests",
            },
        )

        if set_response.status_code != 200:
            raise RuntimeError(f"Failed to set NoOp policy: {set_response.text}")

        result = set_response.json()
        if not result.get("success"):
            raise RuntimeError(f"Failed to set NoOp policy: {result.get('error')}")

        # Give the policy a moment to activate
        await asyncio.sleep(0.1)

        yield "NoOpPolicy"


# === Helper Functions ===


def parse_openai_sse_stream(lines: list[str]) -> list[dict]:
    """Parse OpenAI SSE format: 'data: {json}' lines.

    Returns list of parsed JSON objects (excluding [DONE] marker).
    """
    chunks = []
    for line in lines:
        if line.startswith("data: "):
            data = line[6:].strip()  # Remove 'data: ' prefix
            if data == "[DONE]":
                continue
            try:
                chunks.append(json.loads(data))
            except json.JSONDecodeError:
                # Skip malformed chunks
                continue
    return chunks


def parse_anthropic_sse_stream(lines: list[str]) -> list[tuple[str, dict]]:
    """Parse Anthropic SSE format: 'event: type\\ndata: {json}' pairs.

    Returns list of (event_type, parsed_data) tuples.
    """
    events = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Look for event line
        if line.startswith("event: "):
            event_type = line[7:].strip()

            # Next line should be data
            if i + 1 < len(lines) and lines[i + 1].startswith("data: "):
                data_line = lines[i + 1]
                data = data_line[6:].strip()
                try:
                    parsed = json.loads(data)
                    events.append((event_type, parsed))
                except json.JSONDecodeError:
                    pass
                i += 2
                continue

        i += 1

    return events


def has_done_marker(lines: list[str]) -> bool:
    """Check if OpenAI stream has [DONE] marker."""
    return any(line.strip() == "data: [DONE]" for line in lines)


# === OpenAI Streaming Structure Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_streaming_sse_format(http_client):
    """Validate OpenAI streaming SSE format compliance."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello in 3 words"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        # Should have data lines
        data_lines = [line for line in lines if line.startswith("data: ")]
        assert len(data_lines) > 0, "Should have data lines"

        # Should end with [DONE]
        assert has_done_marker(lines), "OpenAI stream should end with data: [DONE]"

        # All data lines should be valid format
        for line in data_lines:
            assert line.startswith("data: "), f"Invalid SSE format: {line}"
            # Should be followed by blank line in full stream

        # Parse chunks
        chunks = parse_openai_sse_stream(lines)
        assert len(chunks) > 0, "Should have content chunks before [DONE]"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_streaming_chunk_structure(http_client, noop_policy_active):
    """Validate OpenAI chunk structure and required fields."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Count to 3"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        chunks = parse_openai_sse_stream(lines)
        assert len(chunks) > 0

        # First chunk should have role
        first_chunk = chunks[0]
        assert "id" in first_chunk, "Chunk should have id"
        assert "object" in first_chunk, "Chunk should have object"
        assert first_chunk["object"] == "chat.completion.chunk"
        assert "choices" in first_chunk, "Chunk should have choices"
        assert len(first_chunk["choices"]) > 0

        first_choice = first_chunk["choices"][0]
        assert "delta" in first_choice, "Choice should have delta"
        assert "index" in first_choice, "Choice should have index"

        # First chunk must have role
        assert "role" in first_choice["delta"], "First chunk must have role in delta"
        assert first_choice["delta"]["role"] == "assistant", "Role must be assistant"

        # Last chunk should have finish_reason
        last_chunk = chunks[-1]
        last_choice = last_chunk["choices"][0]
        assert "finish_reason" in last_choice, "Last chunk should have finish_reason"
        assert last_choice["finish_reason"] in ["stop", "length", "tool_calls"], (
            f"Invalid finish_reason: {last_choice['finish_reason']}"
        )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_streaming_chunk_ordering(http_client, noop_policy_active):
    """Validate that OpenAI chunks maintain proper ordering."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say: ABCDEFGH"}],
            "max_tokens": 30,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        chunks = parse_openai_sse_stream(lines)

        # All chunks should have same id
        chunk_ids = {chunk["id"] for chunk in chunks}
        assert len(chunk_ids) == 1, "All chunks should share the same completion id"

        # All chunks must have choices, and choices should maintain index 0 for single-choice requests
        for chunk in chunks:
            assert "choices" in chunk and len(chunk["choices"]) > 0, "All chunks must have choices"
            assert chunk["choices"][0]["index"] == 0, "Choice index must be 0"

        # Finish reason should only appear in last chunk
        finish_reasons = [
            chunk["choices"][0].get("finish_reason")
            for chunk in chunks
            if chunk["choices"][0].get("finish_reason") is not None
        ]
        assert len(finish_reasons) == 1, "Only last chunk should have finish_reason"


# === Anthropic Streaming Structure Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_streaming_event_lifecycle(http_client):
    """Validate Anthropic streaming event lifecycle: start → content → stop."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hello in 3 words"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        events = parse_anthropic_sse_stream(lines)
        assert len(events) > 0, "Should have events"

        event_types = [event_type for event_type, _ in events]

        # Required event sequence
        assert "message_start" in event_types, "Should have message_start"
        assert "content_block_start" in event_types, "Should have content_block_start"
        assert "content_block_delta" in event_types, "Should have content_block_delta"
        assert "message_stop" in event_types, "Should have message_stop"

        # Event ordering
        assert event_types[0] == "message_start", "First event should be message_start"
        assert event_types[-1] == "message_stop", "Last event should be message_stop"

        # content_block_start should come before content_block_delta
        start_idx = event_types.index("content_block_start")
        first_delta_idx = event_types.index("content_block_delta")
        assert start_idx < first_delta_idx, "content_block_start should precede content_block_delta"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_streaming_message_start_structure(http_client):
    """Validate message_start event structure."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 10,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        events = parse_anthropic_sse_stream(lines)

        # Find message_start
        message_start = next((data for event_type, data in events if event_type == "message_start"), None)
        assert message_start is not None, "Should have message_start event"

        # Validate structure
        assert message_start["type"] == "message_start"
        assert "message" in message_start

        message = message_start["message"]
        assert "id" in message, "Message should have id"
        assert "type" in message, "Message should have type"
        assert message["type"] == "message"
        assert "role" in message, "Message should have role"
        assert message["role"] == "assistant"
        assert "content" in message, "Message should have content array"
        assert isinstance(message["content"], list)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_streaming_content_block_structure(http_client):
    """Validate content block event structure and indices."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say: ABC"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        events = parse_anthropic_sse_stream(lines)

        # Find content_block_start
        content_starts = [(event_type, data) for event_type, data in events if event_type == "content_block_start"]
        assert len(content_starts) > 0, "Should have content_block_start"

        for event_type, data in content_starts:
            assert data["type"] == "content_block_start"
            assert "index" in data, "content_block_start should have index"
            assert "content_block" in data, "content_block_start should have content_block"

            content_block = data["content_block"]
            assert "type" in content_block, "content_block should have type"
            assert content_block["type"] in ["text", "tool_use"], f"Invalid content_block type: {content_block['type']}"

        # Find content_block_delta
        content_deltas = [(event_type, data) for event_type, data in events if event_type == "content_block_delta"]
        assert len(content_deltas) > 0, "Should have content_block_delta"

        for event_type, data in content_deltas:
            assert data["type"] == "content_block_delta"
            assert "index" in data, "content_block_delta should have index"
            assert "delta" in data, "content_block_delta should have delta"

            delta = data["delta"]
            assert "type" in delta, "delta should have type"
            # Delta type should be text_delta or input_json_delta
            assert delta["type"] in ["text_delta", "input_json_delta"], f"Invalid delta type: {delta['type']}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_streaming_message_stop_structure(http_client):
    """Validate message_stop and message_delta events."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hi"}],
            "max_tokens": 10,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        events = parse_anthropic_sse_stream(lines)

        # Find message_delta (has stop_reason)
        message_deltas = [(event_type, data) for event_type, data in events if event_type == "message_delta"]

        # Anthropic streams must have message_delta with stop_reason
        assert len(message_deltas) >= 1, "Should have at least one message_delta event"

        # Last message_delta must have stop_reason
        last_message_delta = message_deltas[-1][1]
        assert last_message_delta["type"] == "message_delta"
        assert "delta" in last_message_delta
        assert "stop_reason" in last_message_delta["delta"], "Last message_delta must have stop_reason"
        assert last_message_delta["delta"]["stop_reason"] in [
            "end_turn",
            "max_tokens",
            "stop_sequence",
            "tool_use",
        ], f"Invalid stop_reason: {last_message_delta['delta']['stop_reason']}"

        # Find message_stop
        message_stops = [(event_type, data) for event_type, data in events if event_type == "message_stop"]
        assert len(message_stops) == 1, "Should have exactly one message_stop"

        event_type, data = message_stops[0]
        assert data["type"] == "message_stop"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_streaming_sse_format_compliance(http_client):
    """Validate Anthropic SSE format: 'event: type\\ndata: json\\n\\n'."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 10,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        # Should have event lines
        event_lines = [line for line in lines if line.startswith("event: ")]
        assert len(event_lines) > 0, "Should have event lines"

        # Each event line should be followed by a data line
        i = 0
        while i < len(lines):
            if lines[i].startswith("event: "):
                # Next non-empty line should be data
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1

                if j < len(lines):
                    assert lines[j].startswith("data: "), (
                        f"Event at line {i} should be followed by data line, got: {lines[j]}"
                    )
            i += 1


# === Cross-Format Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_anthropic_backend_preserves_openai_format(http_client):
    """Verify OpenAI client API with Anthropic backend produces OpenAI SSE format."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 10,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        # Should use OpenAI format (data: lines, not event: lines)
        data_lines = [line for line in lines if line.startswith("data: ")]
        event_lines = [line for line in lines if line.startswith("event: ")]

        assert len(data_lines) > 0, "Should have OpenAI-style data lines"
        assert len(event_lines) == 0, "Should NOT have Anthropic-style event lines"

        # Should have [DONE] marker
        assert has_done_marker(lines), "OpenAI format should have [DONE] marker"

        # Should parse as OpenAI chunks
        chunks = parse_openai_sse_stream(lines)
        assert len(chunks) > 0


# NOTE: test_anthropic_client_openai_backend_preserves_anthropic_format removed
# Cross-format routing (OpenAI model to Anthropic endpoint) not supported in current
# architecture. Phase 2 work per PR #169. See PR #172 for similar test removals.


# === Tool Call Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_streaming_tool_call_structure(http_client, noop_policy_active):
    """Validate OpenAI streaming tool call chunk structure.

    Reference: _scratch/openai_tool_call_chunks.json shows:
    - First chunk has: role=assistant, tool_calls[0] with id, type=function, function.name, function.arguments=""
    - Subsequent chunks: tool_calls[0].function.arguments with JSON fragments
    - Last chunk: finish_reason="tool_calls"
    """
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "What's the weather in SF? Use the get_weather tool."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string", "description": "City name"},
                            },
                            "required": ["location"],
                        },
                    },
                }
            ],
            "max_tokens": 50,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        chunks = parse_openai_sse_stream(lines)
        assert len(chunks) > 0, "Should have chunks"

        # Find chunks with tool_calls
        tool_call_chunks = [
            chunk for chunk in chunks if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("tool_calls")
        ]

        assert len(tool_call_chunks) > 0, "Should have tool call chunks"

        # FIRST tool call chunk: should have id, type, function.name, function.arguments=""
        first_tool_chunk = tool_call_chunks[0]
        assert "choices" in first_tool_chunk
        assert len(first_tool_chunk["choices"]) > 0

        first_choice = first_tool_chunk["choices"][0]
        assert "delta" in first_choice
        assert "tool_calls" in first_choice["delta"]

        tool_calls = first_choice["delta"]["tool_calls"]
        assert len(tool_calls) == 1, "Should have exactly one tool call in first chunk"

        first_tool = tool_calls[0]
        assert first_tool["index"] == 0, "First tool call should have index 0"
        assert "id" in first_tool, "First tool call chunk must have id"
        assert first_tool["id"].startswith("call_"), f"Tool call id should start with 'call_': {first_tool['id']}"
        assert first_tool["type"] == "function", "Tool call type must be 'function'"
        assert "function" in first_tool, "First tool call chunk must have function"
        assert first_tool["function"]["name"] == "get_weather", "Function name must be present in first chunk"
        assert "arguments" in first_tool["function"], "Function must have arguments field (even if empty)"

        # SUBSEQUENT tool call chunks: should have function.arguments deltas
        argument_chunks = [
            chunk
            for chunk in tool_call_chunks[1:]  # Skip first chunk
            if chunk["choices"][0]["delta"]["tool_calls"][0].get("function", {}).get("arguments")
        ]

        assert len(argument_chunks) > 0, "Should have argument delta chunks after first chunk"

        # Accumulate arguments
        all_arguments = first_tool["function"]["arguments"]  # Start with first chunk (usually "")
        for chunk in argument_chunks:
            args_fragment = chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
            all_arguments += args_fragment

        # Arguments should form valid JSON
        args = json.loads(all_arguments)
        assert "location" in args, "Tool arguments must have location parameter"

        # LAST chunk: should have finish_reason="tool_calls"
        last_chunk = chunks[-1]
        assert "choices" in last_chunk
        assert len(last_chunk["choices"]) > 0
        assert last_chunk["choices"][0]["finish_reason"] == "tool_calls", (
            "Tool call stream must end with finish_reason='tool_calls'"
        )


@pytest.fixture(scope="module")
async def tool_call_judge_policy_active():
    """Ensure ToolCallJudgePolicy is active for testing buffered tool call emission.

    This policy buffers tool calls and re-emits them using create_tool_call_chunk,
    which tests the policy-generated streaming format (not passthrough from LiteLLM).
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        admin_headers = {"Authorization": f"Bearer {ADMIN_API_KEY}"}

        # Set ToolCallJudgePolicy using the /admin/policy/set endpoint
        set_response = await client.post(
            f"{GATEWAY_URL}/admin/policy/set",
            headers=admin_headers,
            json={
                "policy_class_ref": "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
                "config": {
                    "model": "claude-haiku-4-5",
                    "probability_threshold": 0.99,  # High threshold = allow most tool calls
                    "temperature": 0.0,
                    "max_tokens": 256,
                },
                "enabled_by": "e2e-streaming-tests",
            },
        )

        if set_response.status_code != 200:
            raise RuntimeError(f"Failed to set ToolCallJudgePolicy: {set_response.text}")

        result = set_response.json()
        if not result.get("success"):
            raise RuntimeError(f"Failed to set ToolCallJudgePolicy: {result.get('error')}")

        # Give the policy a moment to activate
        await asyncio.sleep(0.1)

        yield "ToolCallJudgePolicy"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_streaming_tool_use_structure(http_client, noop_policy_active):
    """Validate Anthropic streaming tool use event structure.

    Reference: _scratch/anthropic_raw_sse.txt shows:
    - message_start
    - content_block_start with type=tool_use, id=toolu_*, name=tool_name, input={}
    - content_block_delta with type=input_json_delta, partial_json fragments
    - content_block_stop
    - message_delta with stop_reason=tool_use
    - message_stop

    Using "write a file" prompt which reliably triggers tool use.
    """
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": "Write a file called hello.txt with the content 'Hello, World!'",
                }
            ],
            "tools": [
                {
                    "name": "write_file",
                    "description": "Write content to a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "description": "Name of the file"},
                            "content": {"type": "string", "description": "Content to write"},
                        },
                        "required": ["filename", "content"],
                    },
                }
            ],
            "max_tokens": 150,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        events = parse_anthropic_sse_stream(lines)
        assert len(events) > 0, "Should have events"

        event_types = [event_type for event_type, _ in events]

        # Should have standard message lifecycle
        assert event_types[0] == "message_start", "Must start with message_start"
        assert event_types[-1] == "message_stop", "Must end with message_stop"

        # Find content_block_start with tool_use
        tool_start_events = [
            (event_type, data)
            for event_type, data in events
            if event_type == "content_block_start" and data.get("content_block", {}).get("type") == "tool_use"
        ]

        assert len(tool_start_events) == 1, "Should have exactly one tool_use content_block_start"

        event_type, tool_start = tool_start_events[0]
        assert tool_start["type"] == "content_block_start"
        assert "index" in tool_start
        assert "content_block" in tool_start

        content_block = tool_start["content_block"]
        assert content_block["type"] == "tool_use"
        assert "id" in content_block
        assert content_block["id"].startswith("toolu_"), f"Tool use id must start with 'toolu_': {content_block['id']}"
        assert content_block["name"] == "write_file", "Tool name must be present"
        assert "input" in content_block, "Tool use must have input object"
        assert content_block["input"] == {}, "Initial input should be empty dict"

        # Find content_block_delta with input_json_delta
        tool_delta_events = [
            (event_type, data)
            for event_type, data in events
            if event_type == "content_block_delta" and data.get("delta", {}).get("type") == "input_json_delta"
        ]

        assert len(tool_delta_events) > 0, "Should have input_json_delta events"

        # Accumulate partial JSON
        all_json = "".join(data["delta"]["partial_json"] for event_type, data in tool_delta_events)

        # Accumulated JSON should be valid and complete
        assert len(all_json) > 0, "Tool input JSON must not be empty"
        tool_input = json.loads(all_json)
        assert "filename" in tool_input, "Tool input must have filename parameter"
        assert "content" in tool_input, "Tool input must have content parameter"

        # Find content_block_stop for the tool use
        tool_stop_events = [(event_type, data) for event_type, data in events if event_type == "content_block_stop"]

        assert len(tool_stop_events) >= 1, "Should have content_block_stop"

        # Find message_delta with stop_reason=tool_use
        message_delta_events = [(event_type, data) for event_type, data in events if event_type == "message_delta"]

        assert len(message_delta_events) >= 1, "Should have message_delta"

        # Last message_delta should have stop_reason=tool_use
        last_message_delta = message_delta_events[-1][1]
        assert "delta" in last_message_delta
        assert "stop_reason" in last_message_delta["delta"]
        assert last_message_delta["delta"]["stop_reason"] == "tool_use", (
            "Final message_delta must have stop_reason='tool_use'"
        )


@pytest.mark.skip(
    reason="Known limitation: ToolCallJudgePolicy cannot re-emit buffered tool calls in streaming. "
    "See tool_call_judge_policy.py lines 668-676. Needs architectural change to allow "
    "policies to emit multiple events from on_stream_event."
)
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_buffered_tool_call_emits_message_delta(http_client, tool_call_judge_policy_active):
    """Test that policy-buffered tool calls emit proper message_delta with stop_reason.

    NOTE: This test is skipped because ToolCallJudgePolicy has a known limitation where
    it cannot re-emit buffered streaming events after judging. The on_stream_event
    interface only allows returning a single event, but re-emitting a buffered tool
    call requires emitting content_block_start, multiple deltas, and content_block_stop.

    This test documents the desired behavior for when the limitation is fixed.
    """
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": "Call the get_weather tool with location set to 'San Francisco'. Do not respond with text, only use the tool.",
                }
            ],
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get current weather for a location",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                        },
                        "required": ["location"],
                    },
                }
            ],
            "max_tokens": 150,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

        events = parse_anthropic_sse_stream(lines)
        assert len(events) > 0, "Should have events"

        event_types = [event_type for event_type, _ in events]

        # Should have standard message lifecycle
        assert event_types[0] == "message_start", "Must start with message_start"
        assert event_types[-1] == "message_stop", "Must end with message_stop"

        # Find tool_use content_block_start
        tool_start_events = [
            (event_type, data)
            for event_type, data in events
            if event_type == "content_block_start" and data.get("content_block", {}).get("type") == "tool_use"
        ]

        assert len(tool_start_events) >= 1, "Should have at least one tool_use content_block_start"

        # Validate tool call structure
        event_type, tool_start = tool_start_events[0]
        content_block = tool_start["content_block"]
        assert content_block["type"] == "tool_use"
        assert "id" in content_block, "Tool use must have id"
        assert "name" in content_block, "Tool use must have name"
        assert content_block["name"] == "get_weather"

        # Find input_json_delta events
        tool_delta_events = [
            (event_type, data)
            for event_type, data in events
            if event_type == "content_block_delta" and data.get("delta", {}).get("type") == "input_json_delta"
        ]

        assert len(tool_delta_events) > 0, "Should have input_json_delta events"

        # Accumulate and validate JSON
        all_json = "".join(data["delta"]["partial_json"] for event_type, data in tool_delta_events)
        assert len(all_json) > 0, "Tool input JSON must not be empty"
        tool_input = json.loads(all_json)
        assert "location" in tool_input, "Tool input must have location parameter"

        # CRITICAL: Find message_delta with stop_reason=tool_use
        # This is the specific bug we fixed - without this, Claude Code doesn't recognize the tool call
        message_delta_events = [(event_type, data) for event_type, data in events if event_type == "message_delta"]

        assert len(message_delta_events) >= 1, (
            "Must have message_delta event - this was the bug! "
            "Policy-buffered tool calls were not emitting message_delta with stop_reason"
        )

        # Validate the message_delta has proper stop_reason
        last_message_delta = message_delta_events[-1][1]
        assert "delta" in last_message_delta, "message_delta must have delta field"
        assert "stop_reason" in last_message_delta["delta"], (
            "message_delta must have stop_reason - clients like Claude Code require this"
        )
        assert last_message_delta["delta"]["stop_reason"] == "tool_use", (
            f"stop_reason must be 'tool_use', got: {last_message_delta['delta'].get('stop_reason')}"
        )
