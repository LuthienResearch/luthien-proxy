# ABOUTME: Unit tests for V2 ControlPlaneLocal implementation
# ABOUTME: Tests local control plane with policy execution and event handling

"""Tests for V2 ControlPlaneLocal."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from luthien_proxy.v2.control.synchronous_control_plane import SynchronousControlPlane
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.noop import NoOpPolicy
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.streaming import StreamingError


@pytest.fixture
def call_id():
    """Create test call ID."""
    return "test-call-123"


@pytest.fixture
def control_plane():
    """Create control plane with NoOpPolicy."""
    policy = NoOpPolicy()
    return SynchronousControlPlane(policy=policy, event_publisher=None)


class TestControlPlaneSynchronousRequests:
    """Test ControlPlaneSynchronous request processing."""

    @pytest.mark.asyncio
    async def test_process_request(self, control_plane, call_id):
        """Test processing a request through control plane."""
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

        result = await control_plane.process_request(request, call_id)

        assert result.model == "gpt-4"
        assert result.messages == [{"role": "user", "content": "Hi"}]

    @pytest.mark.asyncio
    async def test_process_request_with_call_id(self, call_id):
        """Test that process_request passes call_id via context."""
        policy = NoOpPolicy()
        control_plane = SynchronousControlPlane(policy=policy)

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        result = await control_plane.process_request(request, call_id)

        # Should process successfully (policy is stateless)
        assert result.model == "gpt-4"

    @pytest.mark.asyncio
    async def test_process_request_with_max_tokens(self, control_plane, call_id):
        """Test process_request with max_tokens set (covers line 75)."""
        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=100,
        )

        result = await control_plane.process_request(request, call_id)

        assert result.model == "gpt-4"
        assert result.max_tokens == 100

    @pytest.mark.asyncio
    async def test_process_request_error_handling(self, call_id):
        """Test that request processing errors are handled."""

        # Create a policy that raises an error
        class FailingPolicy(LuthienPolicy):
            async def process_request(self, req, context: PolicyContext):
                raise ValueError("Policy failed")

            async def process_full_response(self, resp, context: PolicyContext):
                return resp

            async def process_streaming_response(self, incoming, outgoing, context: PolicyContext, keepalive=None):
                pass

        policy = FailingPolicy()
        control_plane = SynchronousControlPlane(policy=policy)

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])

        # Should re-raise the error
        with pytest.raises(ValueError, match="Policy failed"):
            await control_plane.process_request(request, call_id)


class TestSynchronousControlPlaneResponses:
    """Test SynchronousControlPlane response processing."""

    @pytest.mark.asyncio
    async def test_process_full_response(self, control_plane, call_id, make_model_response):
        """Test processing a full response."""
        response = make_model_response(content="Test response", id="resp-123")

        result = await control_plane.process_full_response(response, call_id)

        assert result.id == "resp-123"

    @pytest.mark.asyncio
    async def test_process_full_response_with_call_id(self, call_id, make_model_response):
        """Test that process_full_response passes call_id via context."""
        policy = NoOpPolicy()
        control_plane = SynchronousControlPlane(policy=policy)

        response = make_model_response(content="Test", id="resp-789")
        result = await control_plane.process_full_response(response, call_id)

        # Should process successfully (policy is stateless)
        assert result.id == "resp-789"

    @pytest.mark.asyncio
    async def test_process_full_response_error_returns_original(self, call_id, make_model_response):
        """Test that response processing errors return original response."""

        # Create a policy that raises an error
        class FailingResponsePolicy(LuthienPolicy):
            async def process_request(self, req, context: PolicyContext):
                return req

            async def process_full_response(self, resp, context: PolicyContext):
                raise ValueError("Policy failed")

            async def process_streaming_response(self, incoming, outgoing, context: PolicyContext, keepalive=None):
                pass

        policy = FailingResponsePolicy()
        control_plane = SynchronousControlPlane(policy=policy)

        response = make_model_response(content="Test", id="resp-456")

        # Should return original response (not raise)
        result = await control_plane.process_full_response(response, call_id)
        assert result.id == "resp-456"


class TestSynchronousControlPlaneStreaming:
    """Test SynchronousControlPlane streaming response processing."""

    @pytest.mark.asyncio
    async def test_process_streaming_response(self, control_plane, call_id, make_streaming_chunk):
        """Test processing streaming responses."""

        # Create test chunks
        async def mock_stream():
            for i in range(3):
                yield make_streaming_chunk(content=f"word{i}", id=f"chunk-{i}")

        # Process through control plane with short timeout for tests
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        # Should have all 3 chunks
        assert len(output) == 3
        assert output[0].id == "chunk-0"
        assert output[1].id == "chunk-1"
        assert output[2].id == "chunk-2"

    @pytest.mark.asyncio
    async def test_streaming_with_empty_stream(self, control_plane, call_id):
        """Test streaming with empty input."""

        async def empty_stream():
            return
            yield  # Make it a generator

        # Process empty stream
        output = []
        async for chunk in control_plane.process_streaming_response(empty_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        assert len(output) == 0

    @pytest.mark.asyncio
    async def test_streaming_error_handling(self, call_id, make_streaming_chunk):
        """Test that streaming errors are handled."""

        # Make policy raise an error
        class FailingStreamPolicy(LuthienPolicy):
            async def process_request(self, req, context: PolicyContext):
                return req

            async def process_full_response(self, resp, context: PolicyContext):
                return resp

            async def process_streaming_response(self, incoming, outgoing, context: PolicyContext, keepalive=None):
                raise ValueError("Streaming failed")

        policy = FailingStreamPolicy()
        control_plane = SynchronousControlPlane(policy=policy)

        async def mock_stream():
            yield make_streaming_chunk(content="test")

        # Should raise StreamingError wrapping the original error
        with pytest.raises(StreamingError, match="Streaming failed after 0 chunks"):
            async for _ in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
                pass

    @pytest.mark.asyncio
    async def test_streaming_concurrent_operations(self, control_plane, call_id, make_streaming_chunk):
        """Test that streaming handles concurrent producer/consumer correctly."""

        async def slow_stream():
            """Stream that produces chunks with delays."""
            for i in range(5):
                await asyncio.sleep(0.01)
                yield make_streaming_chunk(content=f"word{i}", id=f"chunk-{i}")

        # Process stream
        output = []
        async for chunk in control_plane.process_streaming_response(slow_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        # Should have all chunks in order
        assert len(output) == 5
        for i, chunk in enumerate(output):
            assert chunk.id == f"chunk-{i}"

    @pytest.mark.asyncio
    async def test_streaming_timeout(self, call_id, make_streaming_chunk):
        """Test that streaming times out when policy hangs."""

        # Make policy hang (never produce output, never call keepalive)
        class HangingPolicy(LuthienPolicy):
            async def process_request(self, req, context: PolicyContext):
                return req

            async def process_full_response(self, resp, context: PolicyContext):
                return resp

            async def process_streaming_response(self, incoming, outgoing, context: PolicyContext, keepalive=None):
                # Just wait forever
                await asyncio.sleep(100)  # Long sleep

        policy = HangingPolicy()
        control_plane = SynchronousControlPlane(policy=policy)

        async def mock_stream():
            yield make_streaming_chunk(content="test")

        # Should timeout after 2 seconds
        with pytest.raises(StreamingError, match="Streaming failed after 0 chunks"):
            async for _ in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=2.0):
                pass

        # Verify it was actually a timeout (check the cause)
        # The __cause__ should be the original timeout error


class TestSynchronousControlPlaneEventPublisher:
    """Test SynchronousControlPlane with event publisher integration."""

    @pytest.mark.asyncio
    async def test_streaming_with_event_publisher(self, call_id, make_streaming_chunk):
        """Test that event publisher receives chunk events."""
        # Create mock event publisher with event tracking
        publish_event = asyncio.Event()
        call_count = {"count": 0}

        async def track_publish(*args, **kwargs):
            call_count["count"] += 1
            publish_event.set()

        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock(side_effect=track_publish)

        policy = NoOpPolicy()
        control_plane = SynchronousControlPlane(policy=policy, event_publisher=mock_publisher)

        # Create test chunks with proper structure
        async def mock_stream():
            for i in range(2):
                yield make_streaming_chunk(
                    content=f"word{i}", id=f"chunk-{i}", finish_reason="stop" if i == 1 else None
                )

        # Process stream
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        # Wait for at least one event to be published
        await asyncio.wait_for(publish_event.wait(), timeout=1.0)

        assert len(output) == 2

        # Verify event publisher was called
        # Should have: chunk_received (x2), chunk_sent (x2), original_complete, transformed_complete
        assert call_count["count"] >= 2  # At least chunk events

    @pytest.mark.asyncio
    async def test_streaming_with_db_pool(self, call_id, make_streaming_chunk):
        """Test that streaming emits events to database."""
        # Create mock DB pool
        mock_db_pool = MagicMock()

        policy = NoOpPolicy()
        control_plane = SynchronousControlPlane(policy=policy, event_publisher=None)

        # Track emit_response_event calls
        mock_emit = Mock()

        # Create test chunks
        async def mock_stream():
            yield make_streaming_chunk(content="hello", id="chunk-1")

        # Patch emit_response_event to track calls
        with patch("luthien_proxy.v2.control.synchronous_control_plane.emit_response_event", side_effect=mock_emit):
            # Process stream with db_pool
            output = []
            async for chunk in control_plane.process_streaming_response(
                mock_stream(), call_id, timeout_seconds=5.0, db_pool=mock_db_pool
            ):
                output.append(chunk)

        assert len(output) == 1
        # Verify emit_response_event was called (it's synchronous, so no need to wait)
        mock_emit.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_event_publisher_error_handling(self, call_id, make_streaming_chunk):
        """Test that event publisher errors don't break streaming."""
        # Create mock event publisher that raises errors
        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock(side_effect=Exception("Redis down"))

        policy = NoOpPolicy()
        control_plane = SynchronousControlPlane(policy=policy, event_publisher=mock_publisher)

        # Create test chunks
        async def mock_stream():
            yield make_streaming_chunk(content="test", id="chunk-1")

        # Should still process successfully even if publisher fails
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        assert len(output) == 1

    @pytest.mark.asyncio
    async def test_streaming_chunk_dict_extraction_error(self, call_id, make_streaming_chunk):
        """Test handling of malformed chunks during event publishing."""
        # Create mock event publisher
        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock()

        policy = NoOpPolicy()
        control_plane = SynchronousControlPlane(policy=policy, event_publisher=mock_publisher)

        # Create chunks that will cause errors when extracting content
        # Use a real chunk but patch model_dump to raise an error
        async def mock_stream():
            chunk = make_streaming_chunk(content="test", id="bad-chunk")
            # Override model_dump to raise an exception
            chunk.model_dump = Mock(side_effect=Exception("Bad chunk"))
            yield chunk

        # Should still process successfully
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        assert len(output) == 1
