# ABOUTME: Tests for PolicyExecutor with StreamingChunkAssembler integration
# ABOUTME: Verifies block assembly, state tracking, and chunk processing

"""Tests for PolicyExecutor with block assembly."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ChatCompletionDeltaToolCall as ToolCall
from litellm.types.utils import Delta, Function, ModelResponse, StreamingChoices

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.policies import PolicyContext
from luthien_proxy.v2.streaming.policy_executor import PolicyExecutor


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
    """Create a mock policy with async hook methods."""
    policy = Mock()
    # For now, these are just placeholders - we'll test actual invocation in Step 3
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
async def test_assembler_processes_content_chunks(mock_policy, policy_ctx, obs_ctx):
    """Test that content chunks are processed through assembler."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # All chunks should pass through
    for expected in chunks:
        result = await output_queue.get()
        assert result == expected

    # Verify sentinel
    assert await output_queue.get() is None


@pytest.mark.asyncio
async def test_assembler_tracks_content_block(mock_policy, policy_ctx, obs_ctx):
    """Test that assembler tracks content block state.

    This test verifies the assembler is working, but we don't inspect
    internal state yet - we'll verify hook invocations in Step 3.
    """
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # Verify chunks passed through (assembler doesn't modify them)
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
    assert results[2].choices[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_assembler_handles_tool_calls(mock_policy, policy_ctx, obs_ctx):
    """Test that assembler processes tool call chunks."""
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

    # All chunks should pass through
    results = []
    while True:
        item = await output_queue.get()
        if item is None:
            break
        results.append(item)

    assert len(results) == 4
    # First chunk has tool id and name
    assert results[0].choices[0].delta.tool_calls[0].id == "call_123"
    assert results[0].choices[0].delta.tool_calls[0].function.name == "search"


@pytest.mark.asyncio
async def test_assembler_handles_mixed_content_and_tools(mock_policy, policy_ctx, obs_ctx):
    """Test content followed by tool call."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("Let me search for that."),
        create_content_chunk("", finish_reason=None),  # Empty chunk between blocks
        create_tool_call_chunk(tool_id="call_456", name="search", index=0),
        create_tool_call_chunk(arguments='{"q":"test"}', index=0),
        create_content_chunk("", finish_reason="tool_calls"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    results = []
    while True:
        item = await output_queue.get()
        if item is None:
            break
        results.append(item)

    assert len(results) == 5


@pytest.mark.asyncio
async def test_assembler_preserves_chunk_order(mock_policy, policy_ctx, obs_ctx):
    """Test that chunk order is preserved through assembly."""
    executor = PolicyExecutor()

    chunks = [
        create_content_chunk("1"),
        create_content_chunk("2"),
        create_content_chunk("3"),
        create_content_chunk("4"),
        create_content_chunk("5", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

    # Verify exact order
    for i, expected in enumerate(chunks, 1):
        result = await output_queue.get()
        assert result.choices[0].delta.content == str(i) if i < 5 else "5"

    assert await output_queue.get() is None
