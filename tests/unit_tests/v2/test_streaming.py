# ABOUTME: Unit tests for StreamingOrchestrator
# ABOUTME: Tests streaming coordination with timeout tracking and optional callbacks

"""Tests for StreamingOrchestrator."""

import asyncio

import pytest

from luthien_proxy.v2.control.streaming_orchestrator import StreamingOrchestrator


class TestStreamingOrchestrator:
    """Test StreamingOrchestrator."""

    @staticmethod
    async def _incoming_stream():
        """Generate test chunks for all tests."""
        for i in range(3):
            yield f"chunk-{i}"

    @staticmethod
    async def _processor(incoming, outgoing, keepalive):
        """Simple pass-through processor for all tests."""
        while True:
            try:
                chunk = await incoming.get()
                if chunk is None:
                    break
                await outgoing.put(chunk)
            except asyncio.QueueShutDown:
                break
        outgoing.shutdown()

    @pytest.mark.asyncio
    async def test_process_with_on_complete_success(self):
        """Test streaming with successful on_complete callback."""
        orchestrator = StreamingOrchestrator()
        chunks_seen = []
        callback_chunks = []

        async def on_complete(chunks):
            """Callback that receives all buffered chunks."""
            callback_chunks.extend(chunks)

        # Process stream
        async for chunk in orchestrator.process(
            self._incoming_stream(),
            self._processor,
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

        async def on_complete(chunks):
            """Callback that raises an exception."""
            raise ValueError("Callback error - should be logged but not propagate")

        # Process stream - should complete successfully despite callback error
        async for chunk in orchestrator.process(
            self._incoming_stream(),
            self._processor,
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

        # Process stream without callback
        async for chunk in orchestrator.process(
            self._incoming_stream(),
            self._processor,
            timeout_seconds=5.0,
        ):
            chunks_seen.append(chunk)

        assert chunks_seen == ["chunk-0", "chunk-1", "chunk-2"]

    @pytest.mark.asyncio
    async def test_timeout_on_producer_hang(self):
        """Test that timeout fires when incoming stream hangs."""
        orchestrator = StreamingOrchestrator()

        async def hanging_incoming():
            """Stream that hangs without producing anything."""
            await asyncio.sleep(100)  # Never yields
            yield "never-reached"

        # Should timeout (wrapped in ExceptionGroup by TaskGroup)
        with pytest.raises(ExceptionGroup) as exc_info:
            async for _ in orchestrator.process(
                hanging_incoming(),
                self._processor,
                timeout_seconds=0.1,
            ):
                pass

        # Verify it contains a TimeoutError
        assert any(isinstance(e, TimeoutError) for e in exc_info.value.exceptions)

    @pytest.mark.asyncio
    async def test_timeout_on_processor_hang(self):
        """Test that timeout fires when processor hangs without calling keepalive."""
        orchestrator = StreamingOrchestrator()

        async def hanging_processor(incoming, outgoing, keepalive):
            """Processor that receives data but never outputs or calls keepalive."""
            await asyncio.sleep(100)  # Never processes

        # Should timeout (wrapped in ExceptionGroup by TaskGroup)
        with pytest.raises(ExceptionGroup) as exc_info:
            async for _ in orchestrator.process(
                self._incoming_stream(),
                hanging_processor,
                timeout_seconds=0.1,
            ):
                pass

        # Verify it contains a TimeoutError
        assert any(isinstance(e, TimeoutError) for e in exc_info.value.exceptions)

    @pytest.mark.asyncio
    async def test_keepalive_prevents_timeout(self):
        """Test that calling keepalive resets timeout."""
        orchestrator = StreamingOrchestrator()
        chunks_seen = []

        async def slow_processor(incoming, outgoing, keepalive):
            """Processor that calls keepalive while working slowly."""
            while True:
                try:
                    chunk = await incoming.get()
                    if chunk is None:
                        break
                    # Do slow work but call keepalive
                    await asyncio.sleep(0.05)
                    keepalive()  # Reset timeout
                    await asyncio.sleep(0.05)
                    keepalive()  # Reset timeout again
                    await outgoing.put(chunk)
                except asyncio.QueueShutDown:
                    break
            outgoing.shutdown()

        # Should complete without timeout despite slow processing
        async for chunk in orchestrator.process(
            self._incoming_stream(),
            slow_processor,
            timeout_seconds=0.2,  # Would timeout without keepalive calls
        ):
            chunks_seen.append(chunk)

        assert chunks_seen == ["chunk-0", "chunk-1", "chunk-2"]
