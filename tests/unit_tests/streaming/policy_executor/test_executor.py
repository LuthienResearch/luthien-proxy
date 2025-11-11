# ABOUTME: Unit tests for PolicyExecutor
# ABOUTME: Tests keepalive mechanism and timeout tracking

"""Tests for PolicyExecutor."""

import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.policies import PolicyContext
from luthien_proxy.streaming.policy_executor import PolicyExecutor
from luthien_proxy.streaming.policy_executor.interface import PolicyTimeoutError


class TestPolicyExecutor:
    """Tests for PolicyExecutor."""

    def test_initialization_with_timeout(self):
        """PolicyExecutor initializes with timeout."""
        executor = PolicyExecutor(timeout_seconds=30.0, recorder=NoOpTransactionRecorder())

        assert executor._timeout_monitor.timeout_seconds == 30.0

    def test_initialization_without_timeout(self):
        """PolicyExecutor can be initialized without timeout."""
        executor = PolicyExecutor(recorder=NoOpTransactionRecorder())

        assert executor._timeout_monitor.timeout_seconds is None

    def test_keepalive_resets_timer(self):
        """Calling keepalive() resets the timeout deadline."""
        executor = PolicyExecutor(timeout_seconds=10.0, recorder=NoOpTransactionRecorder())

        # Initial time_until_timeout should be close to the full timeout
        initial_time = executor._timeout_monitor.time_until_timeout()
        assert 9.9 < initial_time <= 10.0  # Should be close to full timeout

        # Wait a bit
        time.sleep(0.15)
        before_keepalive = executor._timeout_monitor.time_until_timeout()
        assert before_keepalive < 9.9  # Deadline approaching

        # Call keepalive (public method)
        executor.keepalive()

        # Time until timeout should reset to full timeout
        after_keepalive = executor._timeout_monitor.time_until_timeout()
        assert 9.9 < after_keepalive <= 10.0  # Back to full timeout

    def test_time_until_timeout_decreases(self):
        """time_until_timeout() decreases as time passes."""
        executor = PolicyExecutor(timeout_seconds=10.0, recorder=NoOpTransactionRecorder())

        time1 = executor._timeout_monitor.time_until_timeout()
        time.sleep(0.1)
        time2 = executor._timeout_monitor.time_until_timeout()

        assert time2 < time1  # Deadline is getting closer
        assert time1 - time2 >= 0.1  # Decreased by at least the sleep time

    def test_multiple_keepalives(self):
        """Multiple keepalive calls each reset the deadline."""
        executor = PolicyExecutor(timeout_seconds=10.0, recorder=NoOpTransactionRecorder())

        # First keepalive
        time.sleep(0.1)
        executor.keepalive()
        assert executor._timeout_monitor.time_until_timeout() > 9.9  # Reset to full timeout

        # Second keepalive
        time.sleep(0.1)
        executor.keepalive()
        assert executor._timeout_monitor.time_until_timeout() > 9.9  # Reset to full timeout

        # Third keepalive
        time.sleep(0.1)
        executor.keepalive()
        assert executor._timeout_monitor.time_until_timeout() > 9.9  # Reset to full timeout


class TestPolicyExecutorTimeoutEnforcement:
    """Tests for PolicyExecutor timeout enforcement."""

    @pytest.fixture
    def policy_ctx(self):
        """Create a PolicyContext."""
        return PolicyContext(transaction_id="test-timeout-123")

    @pytest.fixture
    def obs_ctx(self):
        """Create a mock ObservabilityContext."""
        return Mock(spec=ObservabilityContext)

    @pytest.fixture
    def mock_policy(self):
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

    def create_content_chunk(self, content: str, finish_reason: str | None = None) -> ModelResponse:
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

    async def async_iter_from_list(self, items: list):
        """Convert a list to an async iterator."""
        for item in items:
            yield item

    @pytest.mark.asyncio
    async def test_timeout_monitor_raises_on_timeout(self):
        """Timeout monitor raises PolicyTimeoutError when timeout exceeded."""
        executor = PolicyExecutor(timeout_seconds=0.2, recorder=NoOpTransactionRecorder())

        # Don't call keepalive - let it timeout
        with pytest.raises(PolicyTimeoutError) as exc_info:
            await executor._timeout_monitor.run()

        assert "timed out" in str(exc_info.value).lower()
        assert "0.2" in str(exc_info.value)  # Should mention threshold

    @pytest.mark.asyncio
    async def test_timeout_monitor_no_timeout_when_disabled(self):
        """Timeout monitor waits indefinitely when timeout is None."""
        executor = PolicyExecutor(timeout_seconds=None, recorder=NoOpTransactionRecorder())

        # Create a task that should wait indefinitely
        monitor_task = asyncio.create_task(executor._timeout_monitor.run())

        # Wait a bit to ensure it doesn't raise
        await asyncio.sleep(0.1)

        # Should still be running
        assert not monitor_task.done()

        # Cancel it
        monitor_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor_task

    @pytest.mark.asyncio
    async def test_process_stream_completes_within_timeout(self, mock_policy, policy_ctx, obs_ctx):
        """Stream processing completes successfully when within timeout."""
        executor = PolicyExecutor(timeout_seconds=5.0, recorder=NoOpTransactionRecorder())
        output_queue = asyncio.Queue()

        # Create fast stream
        chunks = [
            self.create_content_chunk("Hello"),
            self.create_content_chunk(" world", finish_reason="stop"),
        ]
        input_stream = self.async_iter_from_list(chunks)

        # Should complete without timeout
        await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

        # Verify we got output
        assert output_queue.qsize() > 0

    @pytest.mark.asyncio
    async def test_process_raises_timeout_on_stalled_stream(self, mock_policy, policy_ctx, obs_ctx):
        """Process raises PolicyTimeoutError when stream stalls."""
        executor = PolicyExecutor(timeout_seconds=0.3, recorder=NoOpTransactionRecorder())
        output_queue = asyncio.Queue()

        # Create a stalled stream that waits too long
        async def stalled_stream():
            yield self.create_content_chunk("Start")
            # Stall for longer than timeout
            await asyncio.sleep(0.5)
            yield self.create_content_chunk("Too late")

        with pytest.raises(PolicyTimeoutError) as exc_info:
            await executor.process(stalled_stream(), output_queue, mock_policy, policy_ctx, obs_ctx)

        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_process_completes_with_slow_chunks_if_keepalive(self, mock_policy, policy_ctx, obs_ctx):
        """Process completes when chunks arrive slowly but keepalive is called."""
        executor = PolicyExecutor(timeout_seconds=0.5, recorder=NoOpTransactionRecorder())
        output_queue = asyncio.Queue()

        # Create stream with delays between chunks
        # But each chunk calls keepalive, so timeout doesn't trigger
        async def slow_stream():
            yield self.create_content_chunk("First")
            await asyncio.sleep(0.3)  # Less than timeout
            yield self.create_content_chunk("Second")
            await asyncio.sleep(0.3)  # Less than timeout
            yield self.create_content_chunk("Third", finish_reason="stop")

        # Should complete without timeout because keepalive called on each chunk
        await executor.process(slow_stream(), output_queue, mock_policy, policy_ctx, obs_ctx)

        # Verify completion
        assert not output_queue.empty()

    @pytest.mark.asyncio
    async def test_timeout_includes_time_information(self, mock_policy, policy_ctx, obs_ctx):
        """PolicyTimeoutError includes timing information for debugging."""
        executor = PolicyExecutor(timeout_seconds=0.2, recorder=NoOpTransactionRecorder())
        output_queue = asyncio.Queue()

        async def stalled_stream():
            yield self.create_content_chunk("Start")
            await asyncio.sleep(0.4)
            yield self.create_content_chunk("End")

        with pytest.raises(PolicyTimeoutError) as exc_info:
            await executor.process(stalled_stream(), output_queue, mock_policy, policy_ctx, obs_ctx)

        error_msg = str(exc_info.value)
        # Should include actual time and threshold
        assert "0." in error_msg  # Some decimal time value
        assert "threshold" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_timeout_monitor_cancels_on_normal_completion(self, mock_policy, policy_ctx, obs_ctx):
        """Timeout monitor is properly cancelled when stream completes normally."""
        executor = PolicyExecutor(timeout_seconds=10.0, recorder=NoOpTransactionRecorder())
        output_queue = asyncio.Queue()

        chunks = [
            self.create_content_chunk("Done", finish_reason="stop"),
        ]
        input_stream = self.async_iter_from_list(chunks)

        # Should complete and cancel monitor without timeout error
        await executor.process(input_stream, output_queue, mock_policy, policy_ctx, obs_ctx)

        # If we get here, monitor was cancelled properly

    @pytest.mark.asyncio
    async def test_policy_hook_errors_propagate_before_timeout(self, policy_ctx, obs_ctx):
        """Policy hook errors propagate immediately, not masked by timeout."""
        executor = PolicyExecutor(timeout_seconds=10.0, recorder=NoOpTransactionRecorder())
        output_queue = asyncio.Queue()

        # Create policy that raises error
        failing_policy = Mock()
        failing_policy.on_chunk_received = AsyncMock(side_effect=ValueError("Policy error"))
        failing_policy.on_stream_complete = AsyncMock()

        chunks = [self.create_content_chunk("Test")]
        input_stream = self.async_iter_from_list(chunks)

        # Should raise ValueError, not timeout
        with pytest.raises(ValueError, match="Policy error"):
            await executor.process(input_stream, output_queue, failing_policy, policy_ctx, obs_ctx)

    @pytest.mark.asyncio
    async def test_policy_can_call_keepalive_through_context(self, policy_ctx, obs_ctx):
        """Policy can call keepalive through StreamingPolicyContext."""
        executor = PolicyExecutor(timeout_seconds=0.5, recorder=NoOpTransactionRecorder())
        output_queue = asyncio.Queue()

        keepalive_called = []

        # Create policy that calls keepalive during processing
        async def on_chunk_with_keepalive(ctx):
            # Policy calls keepalive
            ctx.keepalive()
            keepalive_called.append(True)

        policy = Mock()
        policy.on_chunk_received = AsyncMock(side_effect=on_chunk_with_keepalive)
        policy.on_content_delta = AsyncMock()
        policy.on_content_complete = AsyncMock()
        policy.on_tool_call_delta = AsyncMock()
        policy.on_tool_call_complete = AsyncMock()
        policy.on_finish_reason = AsyncMock()
        policy.on_stream_complete = AsyncMock()

        chunks = [
            self.create_content_chunk("Test1"),
            self.create_content_chunk("Test2", finish_reason="stop"),
        ]
        input_stream = self.async_iter_from_list(chunks)

        # Should complete without timeout even though policy calls keepalive
        await executor.process(input_stream, output_queue, policy, policy_ctx, obs_ctx)

        # Verify keepalive was called by policy
        assert len(keepalive_called) == 2  # Once per chunk
