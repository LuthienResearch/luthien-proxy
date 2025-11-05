# ABOUTME: Tests for StreamingPolicyExecutor
# ABOUTME: Tests clean policy execution with assembler, hooks, egress queue, and timeout

"""Tests for StreamingPolicyExecutor."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.policy_executor.interface import PolicyTimeoutError
from luthien_proxy.v2.streaming.policy_executor.streaming import (
    StreamingPolicyExecutor,
)
from luthien_proxy.v2.streaming.protocol import PolicyContext


@pytest.fixture
def policy_ctx():
    """Create a PolicyContext."""
    return PolicyContext(transaction_id="test-streaming-123")


@pytest.fixture
def obs_ctx():
    """Create a mock ObservabilityContext."""
    return Mock(spec=ObservabilityContext)


@pytest.fixture
def mock_policy():
    """Create a mock policy with async hook methods."""
    policy = Mock()
    policy.on_chunk_received = AsyncMock()
    policy.on_content_delta = AsyncMock()
    policy.on_content_complete = AsyncMock()
    policy.on_tool_call_delta = AsyncMock()
    policy.on_tool_call_complete = AsyncMock()
    policy.on_finish_reason = AsyncMock()
    policy.on_stream_complete = AsyncMock()
    return policy


def create_content_chunk(content: str, finish_reason: str | None = None) -> ModelResponse:
    """Helper to create a content chunk."""
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
async def test_streaming_executor_basic_flow(mock_policy, policy_ctx, obs_ctx):
    """Test basic chunk flow through streaming executor."""
    executor = StreamingPolicyExecutor(policy=mock_policy)

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # All chunks should pass through
    results = []
    while True:
        item = await output_queue.get()
        if item is None:
            break
        results.append(item)

    assert len(results) == 3
    assert results[0].choices[0].delta.content == "Hello"
    assert results[1].choices[0].delta.content == " world"
    assert results[2].choices[0].delta.content == "!"


@pytest.mark.asyncio
async def test_streaming_executor_calls_all_hooks(mock_policy, policy_ctx, obs_ctx):
    """Test that all appropriate policy hooks are called."""
    executor = StreamingPolicyExecutor(policy=mock_policy)

    chunks = [
        create_content_chunk("Hi", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # Verify hooks were called
    assert mock_policy.on_chunk_received.call_count == 1
    assert mock_policy.on_content_delta.call_count >= 1
    assert mock_policy.on_finish_reason.call_count == 1
    assert mock_policy.on_stream_complete.call_count == 1


@pytest.mark.asyncio
async def test_streaming_executor_provides_streaming_context(mock_policy, policy_ctx, obs_ctx):
    """Test that policy hooks receive StreamingResponseContext with egress_queue."""
    executor = StreamingPolicyExecutor(policy=mock_policy)

    chunks = [create_content_chunk("Test")]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # Check that on_chunk_received was called with a context
    assert mock_policy.on_chunk_received.called
    call_args = mock_policy.on_chunk_received.call_args
    streaming_ctx = call_args[0][0]

    # Verify it's a StreamingResponseContext with the right fields
    assert streaming_ctx.transaction_id == "test-streaming-123"
    assert streaming_ctx.egress_queue is not None
    assert streaming_ctx.scratchpad == policy_ctx.scratchpad


@pytest.mark.asyncio
async def test_streaming_executor_timeout_without_keepalive(mock_policy, policy_ctx, obs_ctx):
    """Test that timeout is raised when policy doesn't call keepalive."""
    executor = StreamingPolicyExecutor(policy=mock_policy, timeout_seconds=0.2)

    # Create a slow policy hook that doesn't call keepalive
    async def slow_hook(ctx):
        await asyncio.sleep(0.3)  # Longer than timeout

    mock_policy.on_chunk_received = AsyncMock(side_effect=slow_hook)

    chunks = [create_content_chunk("Test")]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    with pytest.raises(PolicyTimeoutError):
        await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)


@pytest.mark.asyncio
async def test_streaming_executor_no_timeout_with_keepalive(mock_policy, policy_ctx, obs_ctx):
    """Test that timeout is avoided when policy calls keepalive."""
    executor = StreamingPolicyExecutor(policy=mock_policy, timeout_seconds=0.2)

    # Create a slow policy hook that DOES call keepalive
    async def slow_hook_with_keepalive(ctx):
        for _ in range(5):
            await asyncio.sleep(0.05)
            executor.keepalive()  # Reset timeout

    mock_policy.on_chunk_received = AsyncMock(side_effect=slow_hook_with_keepalive)

    chunks = [create_content_chunk("Test")]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    # Should not raise timeout
    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # Verify chunk made it through
    result = await output_queue.get()
    assert result.choices[0].delta.content == "Test"


@pytest.mark.asyncio
async def test_streaming_executor_no_timeout_when_disabled(mock_policy, policy_ctx, obs_ctx):
    """Test that timeout monitoring is disabled when timeout_seconds=None."""
    executor = StreamingPolicyExecutor(policy=mock_policy, timeout_seconds=None)

    # Create a slow policy hook
    async def very_slow_hook(ctx):
        await asyncio.sleep(0.5)

    mock_policy.on_chunk_received = AsyncMock(side_effect=very_slow_hook)

    chunks = [create_content_chunk("Test")]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    # Should not raise timeout since it's disabled
    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    result = await output_queue.get()
    assert result.choices[0].delta.content == "Test"


@pytest.mark.asyncio
async def test_streaming_executor_empty_stream(mock_policy, policy_ctx, obs_ctx):
    """Test handling of empty input stream."""
    executor = StreamingPolicyExecutor(policy=mock_policy)

    input_stream = async_iter_from_list([])
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, policy_ctx, obs_ctx)

    # Should only have None sentinel
    sentinel = await output_queue.get()
    assert sentinel is None

    # on_stream_complete should still be called
    assert mock_policy.on_stream_complete.call_count == 1
