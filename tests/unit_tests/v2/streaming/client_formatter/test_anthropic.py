# ABOUTME: Unit tests for Anthropic ClientFormatter
# ABOUTME: Tests conversion of ModelResponse chunks to Anthropic SSE format with proper event lifecycle

"""Tests for Anthropic client formatter."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.policies import PolicyContext
from luthien_proxy.v2.streaming.client_formatter.anthropic import AnthropicClientFormatter


@pytest.fixture
def obs_ctx():
    """Create a mock ObservabilityContext."""
    return Mock(spec=ObservabilityContext)


@pytest.fixture
def policy_ctx(obs_ctx):
    """Create a PolicyContext."""
    return PolicyContext(transaction_id="test-123", observability=obs_ctx)


@pytest.fixture
def formatter():
    """Create an Anthropic formatter instance."""
    return AnthropicClientFormatter(model_name="claude-3-5-sonnet-20241022")


def create_model_response(content: str = "Hello", finish_reason: str | None = None) -> ModelResponse:
    """Helper to create a ModelResponse chunk."""
    return ModelResponse(
        id="chatcmpl-123",
        choices=[
            StreamingChoices(
                delta=Delta(content=content, role="assistant"),
                finish_reason=finish_reason,
                index=0,
            )
        ],
        created=1234567890,
        model="claude-3-opus-20240229",
        object="chat.completion.chunk",
    )


@pytest.mark.asyncio
async def test_anthropic_formatter_sends_message_start(formatter, policy_ctx, obs_ctx):
    """Test that formatter sends message_start before first chunk."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = create_model_response(content="Hello")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # First event should be message_start
    first_sse = await output_queue.get()
    assert first_sse.startswith("event: message_start\n")
    assert "data: " in first_sse
    assert first_sse.endswith("\n\n")

    # Parse the message_start event
    lines = first_sse.strip().split("\n")
    assert lines[0] == "event: message_start"
    data_line = "\n".join(lines[1:])  # Remaining lines are data
    assert data_line.startswith("data: ")

    json_str = data_line[6:]  # Remove "data: " prefix
    event = json.loads(json_str)

    assert event["type"] == "message_start"
    assert event["message"]["role"] == "assistant"
    assert event["message"]["id"] == f"msg_{policy_ctx.transaction_id}"


@pytest.mark.asyncio
async def test_anthropic_formatter_sends_message_stop(formatter, policy_ctx, obs_ctx):
    """Test that formatter sends message_stop at the end."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = create_model_response(content="Hi", finish_reason="stop")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # Drain queue to find last event (filter out None sentinel)
    events = []
    while not output_queue.empty():
        item = await output_queue.get()
        if item is not None:
            events.append(item)

    # Last event should be message_stop
    last_sse = events[-1]
    assert "event: message_stop" in last_sse

    # Parse message_stop
    lines = last_sse.strip().split("\n")
    assert lines[0] == "event: message_stop"
    data_line = lines[1]
    json_str = data_line[6:]  # Remove "data: "
    event = json.loads(json_str)

    assert event["type"] == "message_stop"


@pytest.mark.asyncio
async def test_anthropic_formatter_content_block_lifecycle(formatter, policy_ctx, obs_ctx):
    """Test proper content_block lifecycle: start -> delta -> stop."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    # Send text chunks
    await input_queue.put(create_model_response(content="Hello"))
    await input_queue.put(create_model_response(content=" world"))
    await input_queue.put(create_model_response(content="!", finish_reason="stop"))
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # Collect all events (filter out None sentinel)
    events = []
    while not output_queue.empty():
        sse_line = await output_queue.get()
        if sse_line is None:
            continue
        # Parse SSE format: "event: <type>\ndata: <json>\n\n"
        lines = sse_line.strip().split("\n")
        event_type_line = lines[0] if lines[0].startswith("event: ") else None
        data_line = lines[1] if len(lines) > 1 and lines[1].startswith("data: ") else lines[0]

        if data_line.startswith("data: "):
            json_str = data_line[6:]
            event = json.loads(json_str)
            events.append((event_type_line, event))

    # Should have: message_start, content_block_start, 3x content_block_delta, message_stop
    # Note: content_block_stop and message_delta are optional depending on chunk structure
    event_types = [e[1]["type"] for e in events]

    assert "message_start" in event_types
    assert "content_block_start" in event_types
    assert "content_block_delta" in event_types
    assert "message_stop" in event_types

    # Verify event ordering: message_start comes first, message_stop comes last
    assert event_types[0] == "message_start"
    assert event_types[-1] == "message_stop"


@pytest.mark.asyncio
async def test_anthropic_formatter_content_block_indices(formatter, policy_ctx, obs_ctx):
    """Test that content blocks have proper sequential indices."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    # Single text block
    await input_queue.put(create_model_response(content="Test"))
    await input_queue.put(create_model_response(content="", finish_reason="stop"))
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # Find content_block_start event
    found_start = False
    while not output_queue.empty():
        sse_line = await output_queue.get()
        if "content_block_start" in sse_line:
            # Extract JSON
            lines = sse_line.strip().split("\n")
            data_line = [line for line in lines if line.startswith("data: ")][0]
            json_str = data_line[6:]
            event = json.loads(json_str)

            assert event["type"] == "content_block_start"
            assert "index" in event
            assert event["index"] == 0  # First block
            found_start = True
            break

    assert found_start, "Should have found content_block_start event"


@pytest.mark.asyncio
async def test_anthropic_formatter_sse_format_with_event_type(formatter, policy_ctx, obs_ctx):
    """Test that Anthropic SSE format includes event type."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = create_model_response(content="Test")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # First event (message_start)
    sse_line = await output_queue.get()

    # Anthropic SSE format: "event: <type>\ndata: <json>\n\n"
    assert sse_line.startswith("event: ")
    assert "\ndata: " in sse_line
    assert sse_line.endswith("\n\n")

    lines = sse_line.strip().split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("event: ")
    assert lines[1].startswith("data: ")


@pytest.mark.asyncio
async def test_anthropic_formatter_empty_queue(formatter, policy_ctx, obs_ctx):
    """Test formatter handles empty input gracefully."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # Should have only None sentinel (no events - not even message_start if no chunks)
    sentinel = await output_queue.get()
    assert sentinel is None
    assert output_queue.empty()


@pytest.mark.asyncio
async def test_anthropic_formatter_finish_reason_mapping(formatter, policy_ctx, obs_ctx):
    """Test that OpenAI finish reasons map to Anthropic stop reasons."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = create_model_response(content="", finish_reason="stop")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # Find message_delta event with stop_reason
    found_delta = False
    while not output_queue.empty():
        sse_line = await output_queue.get()
        if "message_delta" in sse_line:
            lines = sse_line.strip().split("\n")
            data_line = [line for line in lines if line.startswith("data: ")][0]
            json_str = data_line[6:]
            event = json.loads(json_str)

            assert event["type"] == "message_delta"
            # OpenAI "stop" -> Anthropic "end_turn"
            assert event["delta"]["stop_reason"] == "end_turn"
            found_delta = True
            break

    assert found_delta, "Should have found message_delta with stop_reason"
