# ABOUTME: Unit tests for V2 ControlPlaneLocal implementation
# ABOUTME: Tests local control plane with policy execution and event handling

"""Tests for V2 ControlPlaneLocal."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from luthien_proxy.v2.control.local import ControlPlaneLocal
from luthien_proxy.v2.control.models import RequestMetadata, StreamingError
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.noop import NoOpPolicy


@pytest.fixture
def request_metadata():
    """Create test request metadata."""
    return RequestMetadata(
        call_id="test-call-123",
        timestamp=datetime.now(timezone.utc),
        api_key_hash="test-hash",
    )


@pytest.fixture
def control_plane():
    """Create control plane with NoOpPolicy."""
    policy = NoOpPolicy()
    return ControlPlaneLocal(policy=policy, db_pool=None, redis_client=None)


class TestControlPlaneLocalRequests:
    """Test ControlPlaneLocal request processing."""

    @pytest.mark.asyncio
    async def test_process_request(self, control_plane, request_metadata):
        """Test processing a request through control plane."""
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

        result = await control_plane.process_request(request, request_metadata)

        assert result.model == "gpt-4"
        assert result.messages == [{"role": "user", "content": "Hi"}]

    @pytest.mark.asyncio
    async def test_process_request_sets_call_id(self, request_metadata):
        """Test that process_request sets call_id on policy."""
        policy = NoOpPolicy()
        control_plane = ControlPlaneLocal(policy=policy)

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        await control_plane.process_request(request, request_metadata)

        # Policy should have the call_id set
        assert policy._call_id == "test-call-123"

    @pytest.mark.asyncio
    async def test_process_request_error_handling(self, request_metadata):
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
            await control_plane.process_request(request, request_metadata)

        # Should have created an error event
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 1
        assert events[0].event_type == "request_policy_error"
        assert events[0].severity == "error"


class TestControlPlaneLocalResponses:
    """Test ControlPlaneLocal response processing."""

    @pytest.mark.asyncio
    async def test_process_full_response(self, control_plane, request_metadata):
        """Test processing a full response."""
        mock_response = Mock()
        mock_response.id = "resp-123"
        full_response = FullResponse(response=mock_response)

        result = await control_plane.process_full_response(full_response, request_metadata)

        assert result.response.id == "resp-123"

    @pytest.mark.asyncio
    async def test_process_full_response_sets_call_id(self, request_metadata):
        """Test that process_full_response sets call_id on policy."""
        policy = NoOpPolicy()
        control_plane = ControlPlaneLocal(policy=policy)

        mock_response = Mock()
        full_response = FullResponse(response=mock_response)
        await control_plane.process_full_response(full_response, request_metadata)

        # Policy should have the call_id set
        assert policy._call_id == "test-call-123"

    @pytest.mark.asyncio
    async def test_process_full_response_error_returns_original(self, request_metadata):
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
        result = await control_plane.process_full_response(full_response, request_metadata)
        assert result.response.id == "resp-456"

        # Should have created an error event
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 1
        assert events[0].event_type == "response_policy_error"
        assert events[0].severity == "error"


class TestControlPlaneLocalStreaming:
    """Test ControlPlaneLocal streaming response processing."""

    @pytest.mark.asyncio
    async def test_process_streaming_response(self, control_plane, request_metadata):
        """Test processing streaming responses."""

        # Create test chunks
        async def mock_stream():
            for i in range(3):
                mock_chunk = Mock()
                mock_chunk.id = f"chunk-{i}"
                yield StreamingResponse(chunk=mock_chunk)

        # Process through control plane
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), request_metadata):
            output.append(chunk)

        # Should have all 3 chunks
        assert len(output) == 3
        assert output[0].chunk.id == "chunk-0"
        assert output[1].chunk.id == "chunk-1"
        assert output[2].chunk.id == "chunk-2"

    @pytest.mark.asyncio
    async def test_streaming_emits_events(self, control_plane, request_metadata):
        """Test that streaming emits start/complete events."""

        async def mock_stream():
            for i in range(2):
                mock_chunk = Mock()
                mock_chunk.id = f"chunk-{i}"
                yield StreamingResponse(chunk=mock_chunk)

        # Process stream
        output = []
        async for chunk in control_plane.process_streaming_response(mock_stream(), request_metadata):
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
    async def test_streaming_with_empty_stream(self, control_plane, request_metadata):
        """Test streaming with empty input."""

        async def empty_stream():
            return
            yield  # Make it a generator

        # Process empty stream
        output = []
        async for chunk in control_plane.process_streaming_response(empty_stream(), request_metadata):
            output.append(chunk)

        assert len(output) == 0

        # Should still have start/complete events
        events = await control_plane.get_events("test-call-123")
        assert len(events) == 2
        assert events[0].event_type == "stream_start"
        assert events[1].event_type == "stream_complete"
        assert events[1].details["chunk_count"] == 0

    @pytest.mark.asyncio
    async def test_streaming_error_handling(self, request_metadata):
        """Test that streaming errors are handled."""
        policy = NoOpPolicy()

        # Make policy raise an error
        async def failing_stream(incoming, outgoing):
            raise ValueError("Streaming failed")

        policy.process_streaming_response = failing_stream

        control_plane = ControlPlaneLocal(policy=policy)

        async def mock_stream():
            mock_chunk = Mock()
            yield StreamingResponse(chunk=mock_chunk)

        # Should raise StreamingError wrapping the original error
        with pytest.raises(StreamingError, match="Streaming failed after 0 chunks"):
            async for _ in control_plane.process_streaming_response(mock_stream(), request_metadata):
                pass

        # Should have error event
        events = await control_plane.get_events("test-call-123")
        assert any(e.event_type == "stream_error" for e in events)

    @pytest.mark.asyncio
    async def test_streaming_concurrent_operations(self, control_plane, request_metadata):
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
        async for chunk in control_plane.process_streaming_response(slow_stream(), request_metadata):
            output.append(chunk)

        # Should have all chunks in order
        assert len(output) == 5
        for i, chunk in enumerate(output):
            assert chunk.chunk.id == f"chunk-{i}"


class TestControlPlaneLocalEvents:
    """Test ControlPlaneLocal event handling."""

    def test_event_handler_registration(self, control_plane):
        """Test that policy event handler is registered."""
        # Event handler should be set during init
        assert control_plane.policy._event_handler is not None

    @pytest.mark.asyncio
    async def test_get_events_for_call(self, request_metadata):
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
        await control_plane.process_request(request, request_metadata)

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
        metadata1 = RequestMetadata(
            call_id="call-1",
            timestamp=datetime.now(timezone.utc),
            api_key_hash="hash1",
        )
        metadata2 = RequestMetadata(
            call_id="call-2",
            timestamp=datetime.now(timezone.utc),
            api_key_hash="hash2",
        )

        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        await control_plane.process_request(request, metadata1)
        await control_plane.process_request(request, metadata2)

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
