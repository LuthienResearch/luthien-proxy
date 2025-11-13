# ABOUTME: Tests for PolicyExecutor callback ordering guarantees
# ABOUTME: Validates that policy callbacks are invoked in the correct order per chunk

"""Tests for PolicyExecutor callback ordering.

When each chunk arrives, callbacks run in this order:
1. on_chunk_received
2. on_content_delta or on_tool_call_delta (if in a block)
3. on_content_complete or on_tool_call_complete (if block just completed)
4. on_finish_reason (if finish_reason is present)
5. on_stream_complete (only at the very end)
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ChatCompletionDeltaToolCall as ToolCall
from litellm.types.utils import Delta, Function, ModelResponse, StreamingChoices

from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.policies import PolicyContext
from luthien_proxy.streaming.policy_executor import PolicyExecutor


@pytest.fixture
def policy_ctx():
    """Create a PolicyContext."""
    return PolicyContext(transaction_id="test-ordering-123")


@pytest.fixture
def obs_ctx():
    """Create a mock ObservabilityContext."""
    return Mock(spec=ObservabilityContext)


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
    finish_reason: str | None = None,
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


class CallOrderTracker:
    """Helper to track the order of callback invocations."""

    def __init__(self):
        self.calls = []

    def record(self, callback_name: str, chunk_index: int | None = None):
        """Record a callback invocation."""
        entry = {"callback": callback_name}
        if chunk_index is not None:
            entry["chunk_index"] = chunk_index
        self.calls.append(entry)

    def get_calls_for_chunk(self, chunk_index: int) -> list[str]:
        """Get all callback names for a specific chunk, in order."""
        return [call["callback"] for call in self.calls if call.get("chunk_index") == chunk_index]

    def all_callback_names(self) -> list[str]:
        """Get all callback names in order."""
        return [call["callback"] for call in self.calls]


@pytest.fixture
def tracking_policy():
    """Create a mock policy that tracks call order."""
    tracker = CallOrderTracker()
    policy = Mock()
    policy.short_policy_name = "TrackingPolicy"

    # Create tracking wrappers for each callback
    async def track_chunk_received(ctx):
        chunk_idx = len([c for c in tracker.calls if c["callback"] == "on_chunk_received"])
        tracker.record("on_chunk_received", chunk_idx)

    async def track_content_delta(ctx):
        chunk_idx = len([c for c in tracker.calls if c["callback"] == "on_chunk_received"]) - 1
        tracker.record("on_content_delta", chunk_idx)

    async def track_content_complete(ctx):
        chunk_idx = len([c for c in tracker.calls if c["callback"] == "on_chunk_received"]) - 1
        tracker.record("on_content_complete", chunk_idx)

    async def track_tool_call_delta(ctx):
        chunk_idx = len([c for c in tracker.calls if c["callback"] == "on_chunk_received"]) - 1
        tracker.record("on_tool_call_delta", chunk_idx)

    async def track_tool_call_complete(ctx):
        chunk_idx = len([c for c in tracker.calls if c["callback"] == "on_chunk_received"]) - 1
        tracker.record("on_tool_call_complete", chunk_idx)

    async def track_finish_reason(ctx):
        chunk_idx = len([c for c in tracker.calls if c["callback"] == "on_chunk_received"]) - 1
        tracker.record("on_finish_reason", chunk_idx)

    async def track_stream_complete(ctx):
        tracker.record("on_stream_complete", None)

    policy.on_chunk_received = AsyncMock(side_effect=track_chunk_received)
    policy.on_content_delta = AsyncMock(side_effect=track_content_delta)
    policy.on_content_complete = AsyncMock(side_effect=track_content_complete)
    policy.on_tool_call_delta = AsyncMock(side_effect=track_tool_call_delta)
    policy.on_tool_call_complete = AsyncMock(side_effect=track_tool_call_complete)
    policy.on_finish_reason = AsyncMock(side_effect=track_finish_reason)
    policy.on_stream_complete = AsyncMock(side_effect=track_stream_complete)

    policy.tracker = tracker
    return policy


def assert_callback_order(chunk_calls: list[str], expected_sequence: list[str], test_name: str):
    """Assert that callbacks appear in the expected order.

    Args:
        chunk_calls: List of callback names for a chunk
        expected_sequence: Expected callback names in order
        test_name: Test name for error messages
    """
    for i, expected_callback in enumerate(expected_sequence):
        assert expected_callback in chunk_calls, f"{test_name}: Missing callback {expected_callback}"

        # Verify this callback comes after all previous callbacks in the sequence
        actual_idx = chunk_calls.index(expected_callback)
        for j in range(i):
            prev_callback = expected_sequence[j]
            if prev_callback in chunk_calls:
                prev_idx = chunk_calls.index(prev_callback)
                assert prev_idx < actual_idx, f"{test_name}: {prev_callback} must come before {expected_callback}"


@pytest.mark.asyncio
async def test_single_content_chunk_ordering(tracking_policy, policy_ctx, obs_ctx):
    """Test: on_chunk_received → on_content_delta for a single content chunk."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [create_content_chunk("Hello")]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    chunk_calls = tracking_policy.tracker.get_calls_for_chunk(0)
    assert_callback_order(chunk_calls, ["on_chunk_received", "on_content_delta"], "single_content_chunk")


@pytest.mark.asyncio
async def test_content_with_finish_reason_ordering(tracking_policy, policy_ctx, obs_ctx):
    """Test: on_chunk_received → on_content_complete → on_finish_reason."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    chunk_calls = tracking_policy.tracker.get_calls_for_chunk(1)
    assert_callback_order(
        chunk_calls, ["on_chunk_received", "on_content_complete", "on_finish_reason"], "content_with_finish_reason"
    )


@pytest.mark.asyncio
async def test_tool_call_chunk_ordering(tracking_policy, policy_ctx, obs_ctx):
    """Test: on_chunk_received → on_tool_call_delta for tool call chunks."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_tool_call_chunk(tool_id="call_123", name="search", index=0),
        create_tool_call_chunk(arguments='{"query": "test"}', index=0),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    chunk_calls = tracking_policy.tracker.get_calls_for_chunk(0)
    assert_callback_order(chunk_calls, ["on_chunk_received", "on_tool_call_delta"], "tool_call_chunk")


@pytest.mark.asyncio
async def test_tool_call_complete_ordering(tracking_policy, policy_ctx, obs_ctx):
    """Test: on_tool_call_complete → on_finish_reason when tool call finishes."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_tool_call_chunk(tool_id="call_123", name="search", index=0),
        create_tool_call_chunk(arguments='{"q":"test"}', index=0),
        create_content_chunk("", finish_reason="tool_calls"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    all_calls = tracking_policy.tracker.all_callback_names()

    if "on_tool_call_complete" in all_calls and "on_finish_reason" in all_calls:
        complete_idx = all_calls.index("on_tool_call_complete")
        finish_idx = all_calls.index("on_finish_reason")
        assert complete_idx < finish_idx, "on_tool_call_complete must come before on_finish_reason"


@pytest.mark.asyncio
async def test_empty_chunk_with_finish_ordering(tracking_policy, policy_ctx, obs_ctx):
    """Test: on_chunk_received → on_finish_reason for empty chunk with only finish_reason."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk("", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    chunk_calls = tracking_policy.tracker.get_calls_for_chunk(1)
    assert_callback_order(chunk_calls, ["on_chunk_received", "on_finish_reason"], "empty_chunk_with_finish")


@pytest.mark.asyncio
async def test_stream_complete_is_last(tracking_policy, policy_ctx, obs_ctx):
    """Test: on_stream_complete is always the final callback invoked."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    all_calls = tracking_policy.tracker.all_callback_names()

    assert "on_stream_complete" in all_calls, "on_stream_complete must be called"
    assert all_calls[-1] == "on_stream_complete", "on_stream_complete must be the last callback"


@pytest.mark.asyncio
async def test_multiple_chunks_each_start_with_on_chunk_received(tracking_policy, policy_ctx, obs_ctx):
    """Test: Every chunk starts with on_chunk_received."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_content_chunk("First"),
        create_content_chunk(" second"),
        create_content_chunk(" third", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    # For each chunk, verify on_chunk_received comes first
    for chunk_idx in range(3):
        chunk_calls = tracking_policy.tracker.get_calls_for_chunk(chunk_idx)
        if len(chunk_calls) > 0:
            assert chunk_calls[0] == "on_chunk_received", f"Chunk {chunk_idx}: on_chunk_received must be first"


@pytest.mark.asyncio
async def test_mixed_content_and_tool_calls(tracking_policy, policy_ctx, obs_ctx):
    """Test: Ordering holds for streams with both content and tool calls."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_content_chunk("Thinking..."),
        create_content_chunk(" done"),
        create_tool_call_chunk(tool_id="call_1", name="search", index=0),
        create_tool_call_chunk(arguments='{"q":"test"}', index=0),
        create_content_chunk("", finish_reason="tool_calls"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    all_calls = tracking_policy.tracker.all_callback_names()

    # Verify on_stream_complete is last
    assert all_calls[-1] == "on_stream_complete", "on_stream_complete must be last"

    # Verify each chunk starts with on_chunk_received
    for chunk_idx in range(5):
        chunk_calls = tracking_policy.tracker.get_calls_for_chunk(chunk_idx)
        if len(chunk_calls) > 0:
            assert chunk_calls[0] == "on_chunk_received"


@pytest.mark.asyncio
async def test_content_deltas_before_content_complete(tracking_policy, policy_ctx, obs_ctx):
    """Test: All on_content_delta calls come before on_content_complete."""
    executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

    chunks = [
        create_content_chunk("Hello"),
        create_content_chunk(" world"),
        create_content_chunk("!", finish_reason="stop"),
    ]
    input_stream = async_iter_from_list(chunks)
    output_queue = asyncio.Queue()

    await executor.process(input_stream, output_queue, tracking_policy, policy_ctx, obs_ctx)

    all_calls = tracking_policy.tracker.all_callback_names()

    # Find all delta and complete indices
    delta_indices = [i for i, call in enumerate(all_calls) if call == "on_content_delta"]
    complete_indices = [i for i, call in enumerate(all_calls) if call == "on_content_complete"]

    if delta_indices and complete_indices:
        last_delta_idx = max(delta_indices)
        first_complete_idx = min(complete_indices)
        assert last_delta_idx < first_complete_idx, "All on_content_delta must come before on_content_complete"
