# ABOUTME: Unit tests for V2 policy handlers
# ABOUTME: Tests PolicyHandler base class and NoOpPolicy implementation

"""Tests for V2 policy handlers."""

from unittest.mock import Mock

import pytest

from luthien_proxy.v2.control.models import PolicyEvent
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.base import DefaultPolicyHandler
from luthien_proxy.v2.policies.noop import NoOpPolicy
from luthien_proxy.v2.streaming import ChunkQueue


class TestDefaultPolicyHandler:
    """Test DefaultPolicyHandler base implementation."""

    @pytest.mark.asyncio
    async def test_default_process_request_passthrough(self):
        """Test that default process_request passes through unchanged."""
        policy = DefaultPolicyHandler()
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

        result = await policy.process_request(request)

        assert result == request
        assert result.model == "gpt-4"
        assert result.messages == [{"role": "user", "content": "Hi"}]

    @pytest.mark.asyncio
    async def test_default_process_full_response_passthrough(self):
        """Test that default process_full_response passes through unchanged."""
        policy = DefaultPolicyHandler(verbose=False)  # Disable verbose to avoid Mock attribute access
        mock_response = Mock()
        mock_response.id = "resp-123"
        full_response = FullResponse(response=mock_response)

        result = await policy.process_full_response(full_response)

        assert result == full_response
        assert result.response.id == "resp-123"

    @pytest.mark.asyncio
    async def test_default_streaming_passthrough(self):
        """Test that default streaming passes all chunks through."""
        policy = DefaultPolicyHandler(verbose=False)  # Disable verbose
        policy.forbidden_words = []  # Disable filtering
        incoming: ChunkQueue[StreamingResponse] = ChunkQueue()
        outgoing: ChunkQueue[StreamingResponse] = ChunkQueue()

        # Create some test chunks with proper delta.content structure
        chunks = []
        for i in range(3):
            mock_chunk = Mock()
            mock_chunk.id = f"chunk-{i}"
            mock_chunk.choices = [Mock(delta=Mock(content=f"word{i}"))]
            chunks.append(StreamingResponse(chunk=mock_chunk))

        # Feed chunks and run policy
        async def feed_chunks():
            for chunk in chunks:
                await incoming.put(chunk)
            await incoming.close()

        # Run policy processor
        import asyncio

        feed_task = asyncio.create_task(feed_chunks())
        policy_task = asyncio.create_task(policy.process_streaming_response(incoming, outgoing))

        # Collect output
        output = []
        while True:
            batch = await outgoing.get_available()
            if not batch:
                break
            output.extend(batch)

        await feed_task
        await policy_task

        # Should have all 3 chunks
        assert len(output) == 3
        assert output[0].chunk.id == "chunk-0"
        assert output[1].chunk.id == "chunk-1"
        assert output[2].chunk.id == "chunk-2"

    def test_set_event_handler(self):
        """Test setting event handler."""
        policy = DefaultPolicyHandler()
        events = []

        def handler(event: PolicyEvent):
            events.append(event)

        policy.set_event_handler(handler)

        # Emit an event
        policy.set_call_id("test-call-123")
        policy.emit_event("test_event", "Test summary", {"key": "value"})

        assert len(events) == 1
        assert events[0].event_type == "test_event"
        assert events[0].call_id == "test-call-123"
        assert events[0].summary == "Test summary"
        assert events[0].details["key"] == "value"

    def test_emit_event_without_handler(self):
        """Test that emit_event works without handler (no-op)."""
        policy = DefaultPolicyHandler()
        policy.set_call_id("test-call-456")

        # Should not raise
        policy.emit_event("test", "Test", {})

    def test_set_call_id(self):
        """Test setting call ID for event emission."""
        policy = DefaultPolicyHandler()
        events = []

        policy.set_event_handler(lambda e: events.append(e))
        policy.set_call_id("call-789")
        policy.emit_event("test", "Test", {})

        assert events[0].call_id == "call-789"


class TestNoOpPolicy:
    """Test NoOpPolicy implementation."""

    @pytest.mark.asyncio
    async def test_noop_process_request(self):
        """Test that NoOpPolicy passes request through unchanged."""
        policy = NoOpPolicy()
        request = Request(
            model="claude-3-opus",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        result = await policy.process_request(request)

        assert result == request
        assert result.model == "claude-3-opus"
        assert result.max_tokens == 100

    @pytest.mark.asyncio
    async def test_noop_process_full_response(self):
        """Test that NoOpPolicy passes response through unchanged."""
        policy = NoOpPolicy()
        mock_response = Mock()
        mock_response.choices = [{"message": {"content": "Hello back"}}]
        full_response = FullResponse(response=mock_response)

        result = await policy.process_full_response(full_response)

        assert result == full_response
        assert result.response.choices[0]["message"]["content"] == "Hello back"

    @pytest.mark.asyncio
    async def test_noop_streaming_response(self):
        """Test that NoOpPolicy passes all streaming chunks through."""
        policy = NoOpPolicy()
        incoming: ChunkQueue[StreamingResponse] = ChunkQueue()
        outgoing: ChunkQueue[StreamingResponse] = ChunkQueue()

        # Create test chunks
        test_chunks = []
        for i in range(5):
            mock_chunk = Mock()
            mock_chunk.id = f"chunk-{i}"
            mock_chunk.choices = [{"delta": {"content": f"word{i}"}}]
            test_chunks.append(StreamingResponse(chunk=mock_chunk))

        # Feed chunks
        async def feed_chunks():
            for chunk in test_chunks:
                await incoming.put(chunk)
            await incoming.close()

        # Run policy
        import asyncio

        feed_task = asyncio.create_task(feed_chunks())
        policy_task = asyncio.create_task(policy.process_streaming_response(incoming, outgoing))

        # Collect output
        output = []
        while True:
            batch = await outgoing.get_available()
            if not batch:
                break
            output.extend(batch)

        await feed_task
        await policy_task

        # Should have all chunks unchanged
        assert len(output) == 5
        for i, chunk in enumerate(output):
            assert chunk.chunk.id == f"chunk-{i}"
            assert chunk.chunk.choices[0]["delta"]["content"] == f"word{i}"

    @pytest.mark.asyncio
    async def test_noop_emits_no_events(self):
        """Test that NoOpPolicy doesn't emit events by default."""
        policy = NoOpPolicy()
        events = []

        policy.set_event_handler(lambda e: events.append(e))
        policy.set_call_id("test-call")

        # Process request
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        await policy.process_request(request)

        # Process response
        mock_response = Mock()
        full_response = FullResponse(response=mock_response)
        await policy.process_full_response(full_response)

        # NoOpPolicy shouldn't emit events
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_noop_streaming_with_empty_input(self):
        """Test NoOpPolicy streaming with empty input."""
        policy = NoOpPolicy()
        incoming: ChunkQueue[StreamingResponse] = ChunkQueue()
        outgoing: ChunkQueue[StreamingResponse] = ChunkQueue()

        # Close immediately
        await incoming.close()

        # Run policy
        import asyncio

        policy_task = asyncio.create_task(policy.process_streaming_response(incoming, outgoing))

        # Collect output
        output = []
        while True:
            batch = await outgoing.get_available()
            if not batch:
                break
            output.extend(batch)

        await policy_task

        # Should have no output
        assert len(output) == 0

    @pytest.mark.asyncio
    async def test_noop_streaming_batching(self):
        """Test that NoOpPolicy correctly handles batched chunks."""
        policy = NoOpPolicy()
        incoming: ChunkQueue[StreamingResponse] = ChunkQueue()
        outgoing: ChunkQueue[StreamingResponse] = ChunkQueue()

        # Put multiple chunks quickly (will be batched)
        chunks = []
        for i in range(10):
            mock_chunk = Mock()
            mock_chunk.id = f"chunk-{i}"
            chunks.append(StreamingResponse(chunk=mock_chunk))

        async def feed_chunks():
            for chunk in chunks:
                await incoming.put(chunk)
            await incoming.close()

        # Run policy
        import asyncio

        feed_task = asyncio.create_task(feed_chunks())
        policy_task = asyncio.create_task(policy.process_streaming_response(incoming, outgoing))

        # Collect output
        output = []
        while True:
            batch = await outgoing.get_available()
            if not batch:
                break
            output.extend(batch)

        await feed_task
        await policy_task

        # Should have all 10 chunks
        assert len(output) == 10
        for i, chunk in enumerate(output):
            assert chunk.chunk.id == f"chunk-{i}"
