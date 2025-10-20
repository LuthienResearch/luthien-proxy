# ABOUTME: Unit tests for V2 policy handlers
# ABOUTME: Tests NoOpPolicy implementation

"""Tests for V2 policy handlers."""

from unittest.mock import Mock

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.policies.noop import NoOpPolicy
from luthien_proxy.v2.streaming import ChunkQueue


def make_context(call_id="test-call"):
    """Helper to create a test PolicyContext."""
    # Create mock span for OpenTelemetry
    mock_span = Mock()
    mock_span.add_event = Mock()  # Track span events

    context = PolicyContext(call_id=call_id, span=mock_span, event_publisher=None)
    context._test_events = []  # For backward compatibility with tests
    return context


class TestNoOpPolicy:
    """Test NoOpPolicy implementation."""

    @pytest.mark.asyncio
    async def test_noop_process_request(self):
        """Test that NoOpPolicy passes request through unchanged."""
        policy = NoOpPolicy()
        context = make_context()
        request = Request(
            model="claude-3-opus",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        result = await policy.process_request(request, context)

        assert result == request
        assert result.model == "claude-3-opus"
        assert result.max_tokens == 100

    @pytest.mark.asyncio
    async def test_noop_process_full_response(self):
        """Test that NoOpPolicy passes response through unchanged."""
        policy = NoOpPolicy()
        context = make_context()
        mock_response = Mock(spec=ModelResponse)
        mock_response.choices = [{"message": {"content": "Hello back"}}]

        result = await policy.process_full_response(mock_response, context)

        assert result == mock_response
        assert result.choices[0]["message"]["content"] == "Hello back"

    @pytest.mark.asyncio
    async def test_noop_streaming_response(self):
        """Test that NoOpPolicy passes all streaming chunks through."""
        policy = NoOpPolicy()
        context = make_context()
        incoming: ChunkQueue[ModelResponse] = ChunkQueue()
        outgoing: ChunkQueue[ModelResponse] = ChunkQueue()

        # Create test chunks
        test_chunks = []
        for i in range(5):
            mock_chunk = Mock(spec=ModelResponse)
            mock_chunk.id = f"chunk-{i}"
            mock_chunk.choices = [{"delta": {"content": f"word{i}"}}]
            test_chunks.append(mock_chunk)

        # Feed chunks
        async def feed_chunks():
            for chunk in test_chunks:
                await incoming.put(chunk)
            await incoming.close()

        # Run policy
        import asyncio

        feed_task = asyncio.create_task(feed_chunks())
        policy_task = asyncio.create_task(policy.process_streaming_response(incoming, outgoing, context))

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
        context = make_context()

        # Process request
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Test"}])
        await policy.process_request(request, context)

        # Process response
        mock_response = Mock(spec=ModelResponse)
        await policy.process_full_response(mock_response, context)

        # NoOpPolicy shouldn't emit events
        assert len(context._test_events) == 0

    @pytest.mark.asyncio
    async def test_noop_streaming_with_empty_input(self):
        """Test NoOpPolicy streaming with empty input."""
        policy = NoOpPolicy()
        context = make_context()
        incoming: ChunkQueue[ModelResponse] = ChunkQueue()
        outgoing: ChunkQueue[ModelResponse] = ChunkQueue()

        # Close immediately
        await incoming.close()

        # Run policy
        import asyncio

        policy_task = asyncio.create_task(policy.process_streaming_response(incoming, outgoing, context))

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
        context = make_context()
        incoming: ChunkQueue[ModelResponse] = ChunkQueue()
        outgoing: ChunkQueue[ModelResponse] = ChunkQueue()

        # Put multiple chunks quickly (will be batched)
        chunks = []
        for i in range(10):
            mock_chunk = Mock(spec=ModelResponse)
            mock_chunk.id = f"chunk-{i}"
            chunks.append(mock_chunk)

        async def feed_chunks():
            for chunk in chunks:
                await incoming.put(chunk)
            await incoming.close()

        # Run policy
        import asyncio

        feed_task = asyncio.create_task(feed_chunks())
        policy_task = asyncio.create_task(policy.process_streaming_response(incoming, outgoing, context))

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
