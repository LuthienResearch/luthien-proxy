# ABOUTME: Unit tests for StreamingOrchestrator
# ABOUTME: Tests streaming coordination with timeout tracking and optional callbacks

"""Tests for StreamingOrchestrator."""

import asyncio

import pytest

from luthien_proxy.v2.control.streaming import StreamingOrchestrator


class TestStreamingOrchestrator:
    """Test StreamingOrchestrator."""

    @pytest.mark.asyncio
    async def test_process_with_on_complete_success(self):
        """Test streaming with successful on_complete callback."""
        orchestrator = StreamingOrchestrator()
        chunks_seen = []
        callback_chunks = []

        async def incoming_stream():
            """Generate test chunks."""
            for i in range(3):
                yield f"chunk-{i}"

        async def processor(incoming, outgoing, keepalive):
            """Simple pass-through processor."""
            while True:
                try:
                    chunk = await incoming.get()
                    if chunk is None:
                        break
                    await outgoing.put(chunk)
                except asyncio.QueueShutDown:
                    break
            outgoing.shutdown()

        async def on_complete(chunks):
            """Callback that receives all buffered chunks."""
            callback_chunks.extend(chunks)

        # Process stream
        async for chunk in orchestrator.process(
            incoming_stream(),
            processor,
            timeout_seconds=5.0,
            on_complete=on_complete,
        ):
            chunks_seen.append(chunk)

        # Verify chunks were processed
        assert chunks_seen == ["chunk-0", "chunk-1", "chunk-2"]
        # Verify callback received all chunks
        assert callback_chunks == ["chunk-0", "chunk-1", "chunk-2"]

    @pytest.mark.asyncio
    async def test_process_with_on_complete_exception(self):
        """Test that on_complete exceptions don't fail the stream."""
        orchestrator = StreamingOrchestrator()
        chunks_seen = []

        async def incoming_stream():
            """Generate test chunks."""
            for i in range(3):
                yield f"chunk-{i}"

        async def processor(incoming, outgoing, keepalive):
            """Simple pass-through processor."""
            while True:
                try:
                    chunk = await incoming.get()
                    if chunk is None:
                        break
                    await outgoing.put(chunk)
                except asyncio.QueueShutDown:
                    break
            outgoing.shutdown()

        async def on_complete(chunks):
            """Callback that raises an exception."""
            raise ValueError("Callback error - should be logged but not propagate")

        # Process stream - should complete successfully despite callback error
        async for chunk in orchestrator.process(
            incoming_stream(),
            processor,
            timeout_seconds=5.0,
            on_complete=on_complete,
        ):
            chunks_seen.append(chunk)

        # Stream should complete successfully despite callback error
        assert chunks_seen == ["chunk-0", "chunk-1", "chunk-2"]

    @pytest.mark.asyncio
    async def test_process_without_on_complete(self):
        """Test streaming without on_complete callback (no buffering)."""
        orchestrator = StreamingOrchestrator()
        chunks_seen = []

        async def incoming_stream():
            """Generate test chunks."""
            for i in range(3):
                yield f"chunk-{i}"

        async def processor(incoming, outgoing, keepalive):
            """Simple pass-through processor."""
            while True:
                try:
                    chunk = await incoming.get()
                    if chunk is None:
                        break
                    await outgoing.put(chunk)
                except asyncio.QueueShutDown:
                    break
            outgoing.shutdown()

        # Process stream without callback
        async for chunk in orchestrator.process(
            incoming_stream(),
            processor,
            timeout_seconds=5.0,
        ):
            chunks_seen.append(chunk)

        assert chunks_seen == ["chunk-0", "chunk-1", "chunk-2"]
