# ABOUTME: Tests for PolicyExecutor policy hook invocations
# ABOUTME: Verifies correct policy methods are called based on chunk type and stream state

"""Tests for PolicyExecutor policy hook invocations."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ChatCompletionDeltaToolCall as ToolCall
from litellm.types.utils import Delta, Function, ModelResponse, StreamingChoices

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.policy_executor import PolicyExecutor
from luthien_proxy.v2.streaming.protocol import PolicyContext


@pytest.fixture
def policy_ctx():
    """Create a PolicyContext."""
    return PolicyContext(transaction_id="test-hook-123")


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


def create_tool_call_chunk(
    tool_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
    index: int = 0,
) -> ModelResponse:
    """Helper to create a tool call chunk."""
    tool_call = ToolCall(
        id=tool_id,
        function=Function(name=name, arguments=arguments) if name or arguments else None,
        index=index,
        type="function",
    )
    return ModelResponse(
        id="chatcmpl-123",
        choices=[
            StreamingChoices(
                delta=Delta(role="assistant", tool_calls=[tool_call]),
                finish_reason=None,
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
async def test_on_chunk_received_called_for_every_chunk(mock_policy, policy_ctx, obs_ctx):
    """Test that on_chunk_received is called for every chunk."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # on_chunk_received should be called 3 times
    assert mock_policy.on_chunk_received.call_count == 3


@pytest.mark.asyncio
async def test_on_content_delta_called_for_content_chunks(mock_policy, policy_ctx, obs_ctx):
    """Test that on_content_delta is called for content chunks."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # on_content_delta should be called for content chunks
    # Note: The exact count depends on how assembler handles finish_reason chunks
    assert mock_policy.on_content_delta.call_count >= 2


@pytest.mark.asyncio
async def test_on_content_complete_called_at_block_end(mock_policy, policy_ctx, obs_ctx):
    """Test that on_content_complete is called when content block completes."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # on_content_complete should be called when block finishes
    assert mock_policy.on_content_complete.call_count >= 1


@pytest.mark.asyncio
async def test_on_tool_call_delta_called_for_tool_chunks(mock_policy, policy_ctx, obs_ctx):
    """Test that on_tool_call_delta is called for tool call chunks."""
    executor = PolicyExecutor()

    chunks = [
        create_tool_call_chunk(tool_id="call_123", name="search", index=0),
        create_tool_call_chunk(arguments='{"query":', index=0),
        create_tool_call_chunk(arguments='"test"}', index=0),
        create_content_chunk("", finish_reason="tool_calls"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # on_tool_call_delta should be called for tool call chunks
    assert mock_policy.on_tool_call_delta.call_count >= 1


@pytest.mark.asyncio
async def test_on_tool_call_complete_called_at_tool_end(mock_policy, policy_ctx, obs_ctx):
    """Test that on_tool_call_complete is called when tool call completes."""
    executor = PolicyExecutor()

    chunks = [
        create_tool_call_chunk(tool_id="call_123", name="search", index=0),
        create_tool_call_chunk(arguments='{"q":"test"}', index=0),
        create_content_chunk("", finish_reason="tool_calls"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # on_tool_call_complete should be called when tool call finishes
    assert mock_policy.on_tool_call_complete.call_count >= 1


@pytest.mark.asyncio
async def test_on_finish_reason_called_when_present(mock_policy, policy_ctx, obs_ctx):
    """Test that on_finish_reason is called when finish_reason appears."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # on_finish_reason should be called once
    assert mock_policy.on_finish_reason.call_count == 1


@pytest.mark.asyncio
async def test_on_stream_complete_called_at_end(mock_policy, policy_ctx, obs_ctx):
    """Test that on_stream_complete is called after all chunks processed."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # on_stream_complete should be called once at the end
    assert mock_policy.on_stream_complete.call_count == 1


@pytest.mark.asyncio
async def test_hooks_called_in_correct_order(mock_policy, policy_ctx, obs_ctx):
    """Test that hooks are called in the correct order."""
    executor = PolicyExecutor()

    # Track call order
    call_order = []

    async def track_chunk_received(ctx):
        call_order.append("chunk_received")

    async def track_content_delta(ctx):
        call_order.append("content_delta")

    async def track_finish_reason(ctx):
        call_order.append("finish_reason")

    async def track_stream_complete(ctx):
        call_order.append("stream_complete")

    mock_policy.on_chunk_received = AsyncMock(side_effect=track_chunk_received)
    mock_policy.on_content_delta = AsyncMock(side_effect=track_content_delta)
    mock_policy.on_finish_reason = AsyncMock(side_effect=track_finish_reason)
    mock_policy.on_stream_complete = AsyncMock(side_effect=track_stream_complete)

    chunks = [
        create_content_chunk("Hi", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # Verify ordering: chunk_received comes first, stream_complete comes last
    assert call_order[0] == "chunk_received"
    assert call_order[-1] == "stream_complete"


@pytest.mark.asyncio
async def test_chunks_still_pass_through_with_hooks(mock_policy, policy_ctx, obs_ctx):
    """Test that chunks still pass through even when hooks are invoked."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # Verify all chunks still made it through
    results = []
    while True:
        item = await output_queue.get()
        if item is None:
            break
        results.append(item)

    assert len(results) == 2
    assert results[0].choices[0].delta.content == "Hello"
    assert results[1].choices[0].delta.content == " world"
