# ABOUTME: Unit tests for OpenAI ClientFormatter
# ABOUTME: Tests conversion of ModelResponse chunks to OpenAI SSE format

"""Tests for OpenAI client formatter."""

import asyncio
import json

import pytest
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk

from luthien_proxy.policies import PolicyContext
from luthien_proxy.streaming.client_formatter.openai import OpenAIClientFormatter


@pytest.fixture
def policy_ctx():
    """Create a PolicyContext."""
    return PolicyContext(transaction_id="test-123")


@pytest.fixture
def formatter():
    """Create an OpenAI formatter instance."""
    return OpenAIClientFormatter(model_name="gpt-4")


@pytest.mark.asyncio
async def test_openai_formatter_basic_flow(formatter, policy_ctx):
    """Test that OpenAI formatter converts chunks to SSE format."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    # Add some chunks to input
    chunk1 = make_streaming_chunk(content="Hello")
    chunk2 = make_streaming_chunk(content=" world")
    chunk3 = make_streaming_chunk(content="!", finish_reason="stop")

    await input_queue.put(chunk1)
    await input_queue.put(chunk2)
    await input_queue.put(chunk3)
    await input_queue.put(None)  # Signal end

    # Process
    await formatter.process(input_queue, output_queue, policy_ctx)

    # Verify output (filter out None sentinel)
    results = []
    while not output_queue.empty():
        item = await output_queue.get()
        if item is not None:
            results.append(item)

    # Should have 3 content chunks + 1 [DONE] marker
    assert len(results) == 4
    assert results[-1] == "data: [DONE]\n\n", "Last item should be [DONE] marker"

    # Each SSE line (except [DONE]) should be "data: {json}\n\n"
    for sse_line in results[:-1]:  # Skip [DONE] marker
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
async def test_openai_formatter_preserves_chunk_data(formatter, policy_ctx):
    """Test that formatter preserves all chunk data."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = make_streaming_chunk(content="Test content", id="chatcmpl-123")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx)

    sse_line = await output_queue.get()
    json_str = sse_line[6:-2]
    result = json.loads(json_str)

    # Verify key fields preserved
    assert result["id"] == "chatcmpl-123"
    assert result["model"] == "gpt-4"
    assert result["choices"][0]["delta"]["content"] == "Test content"


@pytest.mark.asyncio
async def test_openai_formatter_empty_queue(formatter, policy_ctx):
    """Test formatter handles empty input gracefully."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    await input_queue.put(None)  # Immediate end signal

    await formatter.process(input_queue, output_queue, policy_ctx)

    # Should produce [DONE] marker even with no chunks, then None sentinel
    done_marker = await output_queue.get()
    assert done_marker == "data: [DONE]\n\n", "Should send [DONE] even with empty stream"

    sentinel = await output_queue.get()
    assert sentinel is None, "Should send None sentinel after [DONE]"
    assert output_queue.empty()


@pytest.mark.asyncio
async def test_openai_formatter_finish_reason(formatter, policy_ctx):
    """Test that finish_reason is properly preserved."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = make_streaming_chunk(content="Done", finish_reason="stop")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx)

    sse_line = await output_queue.get()
    json_str = sse_line[6:-2]
    result = json.loads(json_str)

    assert result["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_openai_formatter_sse_format_compliance(formatter, policy_ctx):
    """Test SSE format is strictly compliant."""
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    chunk = make_streaming_chunk(content="x")
    await input_queue.put(chunk)
    await input_queue.put(None)

    await formatter.process(input_queue, output_queue, policy_ctx)

    sse_line = await output_queue.get()

    # SSE format requirements:
    # 1. Must start with "data: "
    assert sse_line.startswith("data: ")

    # 2. Must end with double newline
    assert sse_line.endswith("\n\n")

    # 3. No extra whitespace or formatting
    assert sse_line.count("\n") == 2  # Exactly two newlines at the end


@pytest.mark.asyncio
async def test_openai_formatter_sends_done_marker(formatter, policy_ctx):
    """Test that formatter sends [DONE] marker at end of stream.

    Per OpenAI API spec, streaming responses must end with 'data: [DONE]'.
    Reference: scripts/capture_openai_sse.py shows real OpenAI API sends this.
    """
    input_queue = asyncio.Queue()
    output_queue = asyncio.Queue()

    # Add some chunks
    await input_queue.put(make_streaming_chunk(content="Hello"))
    await input_queue.put(make_streaming_chunk(content=" world", finish_reason="stop"))
    await input_queue.put(None)  # End signal

    await formatter.process(input_queue, output_queue, policy_ctx)

    # Collect all output
    results = []
    while not output_queue.empty():
        item = await output_queue.get()
        if item is not None:
            results.append(item)

    # Last item should be [DONE] marker
    assert len(results) >= 2, "Should have at least content chunks"

    # Check if we have a [DONE] marker
    done_marker = "data: [DONE]\n\n"
    has_done = any(item == done_marker for item in results)

    assert has_done, f"OpenAI streaming responses must end with 'data: [DONE]' marker. Got final items: {results[-2:]}"

    # [DONE] should be the last item
    assert results[-1] == done_marker, "[DONE] marker must be the last item in stream"
