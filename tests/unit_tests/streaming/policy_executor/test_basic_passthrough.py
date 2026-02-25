
"""Tests for basic PolicyExecutor pass-through."""

import asyncio

import pytest
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk

from luthien_proxy.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.streaming.policy_executor import PolicyExecutor


@pytest.fixture
def policy_ctx():
    """Create a PolicyContext."""
    return PolicyContext(transaction_id="test-123")


@pytest.fixture
def noop_policy():
    """Create a real NoOpPolicy for pass-through testing."""
    return NoOpPolicy()


async def async_iter_from_list(items: list):
    """Convert a list to an async iterator."""
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_basic_passthrough_single_chunk(noop_policy, policy_ctx):
    """Test that a single chunk passes through unchanged."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    # Create input stream with one chunk
    chunk = make_streaming_chunk(content="Hello")
    input_stream = async_iter_from_list([chunk])

    # Create output queue
    output_queue = asyncio.Queue()

    # Process
    await executor.process(input_stream, output_queue, noop_policy, policy_ctx)

    # Verify output
    result = await output_queue.get()
    assert result == chunk

    # Queue should have None sentinel at end
    sentinel = await output_queue.get()
    assert sentinel is None

    # Queue should be empty
    assert output_queue.empty()


@pytest.mark.asyncio
async def test_basic_passthrough_multiple_chunks(noop_policy, policy_ctx):
    """Test that multiple chunks pass through in order."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    # Create input stream with multiple chunks
    chunks = [
        make_streaming_chunk(content="Hello"),
        make_streaming_chunk(content=" world"),
        make_streaming_chunk(content="!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)

    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, noop_policy, policy_ctx)

    # Verify all chunks passed through in order
    for expected_chunk in chunks:
        result = await output_queue.get()
        assert result == expected_chunk

    # Verify None sentinel
    sentinel = await output_queue.get()
    assert sentinel is None


@pytest.mark.asyncio
async def test_basic_passthrough_empty_stream(noop_policy, policy_ctx):
    """Test that empty stream produces only None sentinel."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    input_stream = async_iter_from_list([])
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, noop_policy, policy_ctx)

    # Should only have None sentinel
    sentinel = await output_queue.get()
    assert sentinel is None
    assert output_queue.empty()


@pytest.mark.asyncio
async def test_basic_passthrough_preserves_chunk_data(noop_policy, policy_ctx):
    """Test that chunk data is preserved exactly."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    # Create chunk with specific data
    original_chunk = make_streaming_chunk(content="Test content")
    input_stream = async_iter_from_list([original_chunk])
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, noop_policy, policy_ctx)

    result = await output_queue.get()

    # Verify all fields preserved
    assert result.id == original_chunk.id
    assert result.model == original_chunk.model
    assert result.choices[0].delta.content == "Test content"
    assert result.choices[0].delta.role == "assistant"


@pytest.mark.asyncio
async def test_basic_passthrough_finish_reason(noop_policy, policy_ctx):
    """Test that finish_reason is preserved."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunk = make_streaming_chunk(content="Done", finish_reason="stop")
    input_stream = async_iter_from_list([chunk])
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, noop_policy, policy_ctx)

    result = await output_queue.get()
    assert result.choices[0].finish_reason == "stop"
