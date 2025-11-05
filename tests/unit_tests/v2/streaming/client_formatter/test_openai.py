# ABOUTME: Unit tests for OpenAI ClientFormatter
# ABOUTME: Tests conversion of ModelResponse chunks to OpenAI SSE format

"""Tests for OpenAI client formatter."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.client_formatter.openai import OpenAIClientFormatter
from luthien_proxy.v2.streaming.protocol import PolicyContext


@pytest.fixture
def policy_ctx():
    """Create a PolicyContext."""
    return PolicyContext(transaction_id="test-123")


@pytest.fixture
def obs_ctx():
    """Create a mock ObservabilityContext."""
    return Mock(spec=ObservabilityContext)


@pytest.fixture
def formatter():
    """Create an OpenAI formatter instance."""
    return OpenAIClientFormatter()


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
        model="gpt-4",
        object="chat.completion.chunk",
    )


@pytest.mark.asyncio
async def test_openai_formatter_basic_flow(formatter, policy_ctx, obs_ctx):
    """Test that OpenAI formatter converts chunks to SSE format."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    # Add some chunks to input
    chunk1 = create_model_response(content="Hello")
    chunk2 = create_model_response(content=" world")
    chunk3 = create_model_response(content="!", finish_reason="stop")

    await input_queue.put(chunk1)
    await input_queue.put(chunk2)
    await input_queue.put(chunk3)
    await input_queue.put(None)  # Signal end

    # Process
    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # Verify output
    results = []
    while not output_queue.empty():
        results.append(await output_queue.get())

    assert len(results) == 3

    # Each SSE line should be "data: {json}\n\n"
    for sse_line in results:
        assert sse_line.startswith("data: ")
        assert sse_line.endswith("\n\n")

        # Extract and parse JSON
        json_str = sse_line[6:-2]  # Remove "data: " prefix and "\n\n" suffix
        chunk_dict = json.loads(json_str)

        # Verify it has expected OpenAI structure
        assert "id" in chunk_dict
        assert "choices" in chunk_dict
        assert "model" in chunk_dict


@pytest.mark.asyncio
async def test_openai_formatter_preserves_chunk_data(formatter, policy_ctx, obs_ctx):
    """Test that formatter preserves all chunk data."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = create_model_response(content="Test content")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    sse_line = await output_queue.get()
    json_str = sse_line[6:-2]
    result = json.loads(json_str)

    # Verify key fields preserved
    assert result["id"] == "chatcmpl-123"
    assert result["model"] == "gpt-4"
    assert result["choices"][0]["delta"]["content"] == "Test content"


@pytest.mark.asyncio
async def test_openai_formatter_empty_queue(formatter, policy_ctx, obs_ctx):
    """Test formatter handles empty input gracefully."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    await input_queue.put(None)  # Immediate end signal

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    # Should produce no output
    assert output_queue.empty()


@pytest.mark.asyncio
async def test_openai_formatter_finish_reason(formatter, policy_ctx, obs_ctx):
    """Test that finish_reason is properly preserved."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = create_model_response(content="Done", finish_reason="stop")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    sse_line = await output_queue.get()
    json_str = sse_line[6:-2]
    result = json.loads(json_str)

    assert result["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_openai_formatter_sse_format_compliance(formatter, policy_ctx, obs_ctx):
    """Test SSE format is strictly compliant."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = create_model_response(content="x")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx, obs_ctx)

    sse_line = await output_queue.get()

    # SSE format requirements:
    # 1. Must start with "data: "
    assert sse_line.startswith("data: ")

    # 2. Must end with double newline
    assert sse_line.endswith("\n\n")

    # 3. No extra whitespace or formatting
    assert sse_line.count("\n") == 2  # Exactly two newlines at the end
