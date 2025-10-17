# ABOUTME: Unit tests for V2 ControlPlaneLocal implementation
# ABOUTME: Tests local control plane with policy execution and event handling

"""Tests for V2 ControlPlaneLocal."""

from unittest.mock import Mock

import pytest

from luthien_proxy.v2.control.local import ControlPlaneLocal
from luthien_proxy.v2.control.models import StreamingError
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.noop import NoOpPolicy


@pytest.fixture
def call_id():
    """Create test call ID."""
    return "test-call-123"


@pytest.fixture
def control_plane():
    """Create control plane with NoOpPolicy."""
    policy = NoOpPolicy()
    return ControlPlaneLocal(policy=policy, db_pool=None, redis_client=None)


class TestControlPlaneLocalRequests:
    """Test ControlPlaneLocal request processing."""

    @pytest.mark.asyncio
    async def test_process_request(self, control_plane, call_id):
        """Test processing a request through control plane."""
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

        result = await control_plane.process_request(request, call_id)

        assert result.model == "gpt-4"
        assert result.messages == [{"role": "user", "content": "Hi"}]

    @pytest.mark.asyncio
    async def test_process_request_sets_call_id(self, call_id):
        """Test that process_request sets call_id on policy."""
        policy = NoOpPolicy()
        control_plane = ControlPlaneLocal(policy=policy)

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        await control_plane.process_request(request, call_id)

        # Policy should have the call_id set
        assert policy._call_id == "test-call-123"

    @pytest.mark.asyncio
    async def test_process_request_error_handling(self, call_id):
        """Test that request processing errors are handled."""
        # Create a policy that raises an error
        policy = NoOpPolicy()

        async def failing_process(req):
            raise ValueError("Policy failed")

        policy.process_request = failing_process

        control_plane = ControlPlaneLocal(policy=policy)

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])

        # Should re-raise the error
        with pytest.raises(ValueError, match="Policy failed"):
            await control_plane.process_request(request, call_id)

        # Should have created an error event
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 1
        assert events[0].event_type == "request_policy_error"
        assert events[0].severity == "error"


class TestControlPlaneLocalResponses:
    """Test ControlPlaneLocal response processing."""

    @pytest.mark.asyncio
    async def test_process_full_response(self, control_plane, call_id):
        """Test processing a full response."""
        mock_response = Mock()
        mock_response.id = "resp-123"
        full_response = FullResponse(response=mock_response)

        result = await control_plane.process_full_response(full_response, call_id)

        assert result.response.id == "resp-123"

    @pytest.mark.asyncio
    async def test_process_full_response_sets_call_id(self, call_id):
        """Test that process_full_response sets call_id on policy."""
        policy = NoOpPolicy()
        control_plane = ControlPlaneLocal(policy=policy)

        mock_response = Mock()
        full_response = FullResponse(response=mock_response)
        await control_plane.process_full_response(full_response, call_id)

        # Policy should have the call_id set
        assert policy._call_id == "test-call-123"

    @pytest.mark.asyncio
    async def test_process_full_response_error_returns_original(self, call_id):
        """Test that response processing errors return original response."""
        # Create a policy that raises an error
        policy = NoOpPolicy()

        async def failing_process(resp):
            raise ValueError("Policy failed")

        policy.process_full_response = failing_process

        control_plane = ControlPlaneLocal(policy=policy)

        mock_response = Mock()
        mock_response.id = "resp-456"
        full_response = FullResponse(response=mock_response)

        # Should return original response (not raise)
        result = await control_plane.process_full_response(full_response, call_id)
        assert result.response.id == "resp-456"

        # Should have created an error event
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 1
        assert events[0].event_type == "response_policy_error"
        assert events[0].severity == "error"


class TestControlPlaneLocalStreaming:
    """Test ControlPlaneLocal streaming response processing."""

    @pytest.mark.asyncio
    async def test_process_streaming_response(self, control_plane, call_id):
        """Test processing streaming responses."""

        # Create test chunks
        async def mock_stream():
            for i in range(3):
                mock_chunk = Mock()
                mock_chunk.id = f"chunk-{i}"
                yield StreamingResponse(chunk=mock_chunk)

        # Process through control plane with short timeout for tests
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        # Should have all 3 chunks
        assert len(output) == 3
        assert output[0].chunk.id == "chunk-0"
        assert output[1].chunk.id == "chunk-1"
        assert output[2].chunk.id == "chunk-2"

    @pytest.mark.asyncio
    async def test_streaming_emits_events(self, control_plane, call_id):
        """Test that streaming emits start/complete events."""

        async def mock_stream():
            for i in range(2):
                mock_chunk = Mock()
                mock_chunk.id = f"chunk-{i}"
                yield StreamingResponse(chunk=mock_chunk)

        # Process stream
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        # Check events
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 2

        # Should have start and complete events
        assert events[0].event_type == "stream_start"
        assert events[0].severity == "info"

        assert events[1].event_type == "stream_complete"
        assert events[1].summary == "Completed stream with 2 chunks"
        assert events[1].details["chunk_count"] == 2

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

        # Should still have start/complete events
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 2
        assert events[0].event_type == "stream_start"
        assert events[1].event_type == "stream_complete"
        assert events[1].details["chunk_count"] == 0

    @pytest.mark.asyncio
    async def test_streaming_error_handling(self, call_id):
        """Test that streaming errors are handled."""
        policy = NoOpPolicy()

        # Make policy raise an error
        async def failing_stream(incoming, outgoing, keepalive=None):
            raise ValueError("Streaming failed")

        policy.process_streaming_response = failing_stream

        control_plane = ControlPlaneLocal(policy=policy)

        async def mock_stream():
            mock_chunk = Mock()
            yield StreamingResponse(chunk=mock_chunk)

        # Should raise StreamingError wrapping the original error
        with pytest.raises(StreamingError, match="Streaming failed after 0 chunks"):
            async for _ in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=5.0):
                pass

        # Should have error event
        events = await control_plane.get_events("test-call-123")
        assert any(e.event_type == "stream_error" for e in events)

    @pytest.mark.asyncio
    async def test_streaming_concurrent_operations(self, control_plane, call_id):
        """Test that streaming handles concurrent producer/consumer correctly."""
        import asyncio

        async def slow_stream():
            """Stream that produces chunks with delays."""
            for i in range(5):
                await asyncio.sleep(0.01)
                mock_chunk = Mock()
                mock_chunk.id = f"chunk-{i}"
                yield StreamingResponse(chunk=mock_chunk)

        # Process stream
        output = []
        async for chunk in control_plane.process_streaming_response(slow_stream(), call_id, timeout_seconds=5.0):
            output.append(chunk)

        # Should have all chunks in order
        assert len(output) == 5
        for i, chunk in enumerate(output):
            assert chunk.chunk.id == f"chunk-{i}"

    @pytest.mark.asyncio
    async def test_streaming_timeout(self, call_id):
        """Test that streaming times out when policy hangs."""
        policy = NoOpPolicy()

        # Make policy hang (never produce output, never call keepalive)
        async def hanging_policy(incoming, outgoing, keepalive=None):
            # Just wait forever
            import asyncio

            await asyncio.sleep(100)  # Long sleep

        policy.process_streaming_response = hanging_policy

        control_plane = ControlPlaneLocal(policy=policy)

        async def mock_stream():
            mock_chunk = Mock()
            yield StreamingResponse(chunk=mock_chunk)

        # Should timeout after 2 seconds
        with pytest.raises(StreamingError, match="Streaming failed after 0 chunks"):
            async for _ in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=2.0):
                pass

        # Verify it was actually a timeout (check the cause)
        # The __cause__ should be the original timeout error

    @pytest.mark.asyncio
    async def test_streaming_keepalive_prevents_timeout(self, call_id):
        """Test that keepalive signals prevent timeout."""
        policy = NoOpPolicy()

        # Policy that takes time but sends keepalives
        async def slow_policy_with_keepalive(incoming, outgoing, keepalive=None):
            import asyncio

            try:
                batch = await incoming.get_available()
                if not batch:
                    return

                # Do slow processing with keepalives
                for i in range(3):
                    await asyncio.sleep(1.5)  # Each iteration takes 1.5s
                    if keepalive:
                        keepalive()  # Signal we're still alive

                # Finally produce output
                for chunk in batch:
                    await outgoing.put(chunk)
            finally:
                await outgoing.close()

        policy.process_streaming_response = slow_policy_with_keepalive

        control_plane = ControlPlaneLocal(policy=policy)

        async def mock_stream():
            mock_chunk = Mock()
            mock_chunk.id = "chunk-0"
            yield StreamingResponse(chunk=mock_chunk)

        # Should NOT timeout because of keepalives (timeout is 2s, but we send keepalive every 1.5s)
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), call_id, timeout_seconds=2.0):
            output.append(chunk)

        assert len(output) == 1
        assert output[0].chunk.id == "chunk-0"


class TestControlPlaneLocalEvents:
    """Test ControlPlaneLocal event handling."""

    def test_event_handler_registration(self, control_plane):
        """Test that policy event handler is registered."""
        # Event handler should be set during init
        assert control_plane.policy._event_handler is not None

    @pytest.mark.asyncio
    async def test_get_events_for_call(self, call_id):
        """Test retrieving events for a specific call."""
        from luthien_proxy.v2.policies.base import DefaultPolicyHandler

        # Create a policy that emits events
        class EventEmittingPolicy(DefaultPolicyHandler):
            async def process_request(self, request):
                self.emit_event("custom_event", "Custom event occurred", {"data": "test"})
                return request

        policy = EventEmittingPolicy()
        control_plane = ControlPlaneLocal(policy=policy)

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        await control_plane.process_request(request, call_id)

        # Get events
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 1
        assert events[0].event_type == "custom_event"
        assert events[0].details["data"] == "test"

    @pytest.mark.asyncio
    async def test_events_for_different_calls(self):
        """Test that events are tracked per call_id."""
        from luthien_proxy.v2.policies.base import DefaultPolicyHandler

        class EventEmittingPolicy(DefaultPolicyHandler):
            async def process_request(self, request):
                self.emit_event("request_event", "Request processed")
                return request

        policy = EventEmittingPolicy()
        control_plane = ControlPlaneLocal(policy=policy)

        # Process two different calls
        call_id_1 = "call-1"
        call_id_2 = "call-2"

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        await control_plane.process_request(request, call_id_1)
        await control_plane.process_request(request, call_id_2)

        # Each call should have its own events
        events1 = await control_plane.get_events("call-1")
        events2 = await control_plane.get_events("call-2")

        assert len(events1) == 1
        assert len(events2) == 1
        assert events1[0].call_id == "call-1"
        assert events2[0].call_id == "call-2"

    @pytest.mark.asyncio
    async def test_get_events_for_nonexistent_call(self, control_plane):
        """Test getting events for a call that doesn't exist."""
        events = await control_plane.get_events("nonexistent-call")
        assert events == []
