# ABOUTME: Tests for PolicyExecutor basic pass-through functionality
# ABOUTME: Verifies chunks flow from input stream to output queue without policy processing

"""Tests for basic PolicyExecutor pass-through."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.policy_executor import PolicyExecutor
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
def mock_policy():
    """Create a mock policy with async hook methods.

    Updated for Step 3: Now that we invoke policy hooks, we need AsyncMock.
    """
    policy = Mock()
    policy.on_chunk_received = AsyncMock()
    policy.on_content_delta = AsyncMock()
    policy.on_content_complete = AsyncMock()
    policy.on_tool_call_delta = AsyncMock()
    policy.on_tool_call_complete = AsyncMock()
    policy.on_finish_reason = AsyncMock()
    policy.on_stream_complete = AsyncMock()
    return policy


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


async def async_iter_from_list(items: list):
    """Convert a list to an async iterator."""
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_basic_passthrough_single_chunk(mock_policy, policy_ctx, obs_ctx):
    """Test that a single chunk passes through unchanged."""
    executor = PolicyExecutor(policy=mock_policy)

    # Create input stream with one chunk
    chunk = create_model_response(content="Hello")
    input_stream = async_iter_from_list([chunk])

    # Create output queue
    output_queue = asyncio.Queue()

    # Process
    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # Verify output
    result = await output_queue.get()
    assert result == chunk

    # Queue should have None sentinel at end
    sentinel = await output_queue.get()
    assert sentinel is None

    # Queue should be empty
    assert output_queue.empty()


@pytest.mark.asyncio
async def test_basic_passthrough_multiple_chunks(mock_policy, policy_ctx, obs_ctx):
    """Test that multiple chunks pass through in order."""
    executor = PolicyExecutor(policy=mock_policy)

    # Create input stream with multiple chunks
    chunks = [
        create_model_response(content="Hello"),
        create_model_response(content=" world"),
        create_model_response(content="!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)

    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # Verify all chunks passed through in order
    for expected_chunk in chunks:
        result = await output_queue.get()
        assert result == expected_chunk

    # Verify None sentinel
    sentinel = await output_queue.get()
    assert sentinel is None


@pytest.mark.asyncio
async def test_basic_passthrough_empty_stream(mock_policy, policy_ctx, obs_ctx):
    """Test that empty stream produces only None sentinel."""
    executor = PolicyExecutor(policy=mock_policy)

    input_stream = async_iter_from_list([])
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # Should only have None sentinel
    sentinel = await output_queue.get()
    assert sentinel is None
    assert output_queue.empty()


@pytest.mark.asyncio
async def test_basic_passthrough_preserves_chunk_data(mock_policy, policy_ctx, obs_ctx):
    """Test that chunk data is preserved exactly."""
    executor = PolicyExecutor(policy=mock_policy)

    # Create chunk with specific data
    original_chunk = create_model_response(content="Test content")
    input_stream = async_iter_from_list([original_chunk])
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    result = await output_queue.get()

    # Verify all fields preserved
    assert result.id == original_chunk.id
    assert result.model == original_chunk.model
    assert result.choices[0].delta.content == "Test content"
    assert result.choices[0].delta.role == "assistant"


@pytest.mark.asyncio
async def test_basic_passthrough_finish_reason(mock_policy, policy_ctx, obs_ctx):
    """Test that finish_reason is preserved."""
    executor = PolicyExecutor(policy=mock_policy)

    chunk = create_model_response(content="Done", finish_reason="stop")
    input_stream = async_iter_from_list([chunk])
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    result = await output_queue.get()
    assert result.choices[0].finish_reason == "stop"
