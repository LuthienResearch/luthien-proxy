# ABOUTME: Unit tests for V2 streaming module (ChunkQueue)
# ABOUTME: Tests queue-based batch processing for reactive streaming

"""Tests for V2 streaming module."""

import asyncio

import pytest

from luthien_proxy.v2.streaming import ChunkQueue


class TestChunkQueue:
    """Test ChunkQueue for batch-oriented consumption."""

    @pytest.mark.asyncio
    async def test_put_and_get_available(self):
        """Test basic put and get_available operations."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Put some items
        await queue.put("chunk1")
        await queue.put("chunk2")
        await queue.put("chunk3")

        # Get all available items
        items = await queue.get_available()
        assert items == ["chunk1", "chunk2", "chunk3"]

    @pytest.mark.asyncio
    async def test_get_available_blocks_until_item_available(self):
        """Test that get_available blocks until at least one item is available."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Start get_available in background (will block)
        get_task = asyncio.create_task(queue.get_available())

        # Give it a moment to start blocking
        await asyncio.sleep(0.01)

        # Task should not be done yet
        assert not get_task.done()

        # Put an item
        await queue.put("chunk1")

        # Now get_available should return
        items = await get_task
        assert items == ["chunk1"]

    @pytest.mark.asyncio
    async def test_get_available_returns_batch(self):
        """Test that get_available returns all immediately available items."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Put multiple items quickly
        await queue.put("chunk1")
        await queue.put("chunk2")
        await queue.put("chunk3")

        # First get_available should return all three
        items = await queue.get_available()
        assert len(items) == 3
        assert items == ["chunk1", "chunk2", "chunk3"]

        # Queue should be empty now
        await queue.close()
        items = await queue.get_available()
        assert items == []

    @pytest.mark.asyncio
    async def test_close_and_empty_result(self):
        """Test that close() causes get_available to return empty list."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Close immediately
        await queue.close()

        # get_available should return empty list
        items = await queue.get_available()
        assert items == []

    @pytest.mark.asyncio
    async def test_close_after_items(self):
        """Test that close() after items still allows retrieval."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Put items
        await queue.put("chunk1")
        await queue.put("chunk2")

        # Close queue
        await queue.close()

        # Should still get items
        items = await queue.get_available()
        assert items == ["chunk1", "chunk2"]

        # Next call should return empty
        items = await queue.get_available()
        assert items == []

    @pytest.mark.asyncio
    async def test_multiple_get_available_calls(self):
        """Test multiple sequential get_available calls."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Put first batch
        await queue.put("chunk1")
        items = await queue.get_available()
        assert items == ["chunk1"]

        # Put second batch
        await queue.put("chunk2")
        await queue.put("chunk3")
        items = await queue.get_available()
        assert items == ["chunk2", "chunk3"]

        # Close and get empty
        await queue.close()
        items = await queue.get_available()
        assert items == []

    @pytest.mark.asyncio
    async def test_concurrent_producer_consumer(self):
        """Test producer and consumer running concurrently."""
        queue: ChunkQueue[int] = ChunkQueue()
        consumed = []

        async def producer():
            """Produce items."""
            for i in range(5):
                await queue.put(i)
                await asyncio.sleep(0.01)  # Small delay
            await queue.close()

        async def consumer():
            """Consume items."""
            while True:
                batch = await queue.get_available()
                if not batch:
                    break
                consumed.extend(batch)

        # Run both concurrently
        await asyncio.gather(producer(), consumer())

        # Should have consumed all items
        assert consumed == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_is_closed_property(self):
        """Test is_closed method."""
        queue: ChunkQueue[str] = ChunkQueue()
        assert not queue.is_closed()

        await queue.put("chunk1")
        assert not queue.is_closed()

        await queue.close()
        assert queue.is_closed()

    @pytest.mark.asyncio
    async def test_get_available_with_slow_producer(self):
        """Test that get_available collects immediately available items."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Put first item
        await queue.put("chunk1")

        # Start get_available (should return immediately with chunk1)
        items = await queue.get_available()
        assert items == ["chunk1"]

        # Put more items with delay
        async def slow_producer():
            await asyncio.sleep(0.02)
            await queue.put("chunk2")
            await queue.put("chunk3")
            await queue.close()

        producer_task = asyncio.create_task(slow_producer())

        # Next get_available should wait for chunk2, then grab chunk3 too
        items = await queue.get_available()
        assert items == ["chunk2", "chunk3"]

        await producer_task

        # Final call should return empty
        items = await queue.get_available()
        assert items == []

    @pytest.mark.asyncio
    async def test_empty_queue_close(self):
        """Test closing an empty queue."""
        queue: ChunkQueue[str] = ChunkQueue()

        # Close without putting anything
        await queue.close()

        # Should return empty immediately
        items = await queue.get_available()
        assert items == []

    @pytest.mark.asyncio
    async def test_typed_queue(self):
        """Test ChunkQueue with different types."""

        # String queue
        str_queue: ChunkQueue[str] = ChunkQueue()
        await str_queue.put("hello")
        items = await str_queue.get_available()
        assert items == ["hello"]
        await str_queue.close()

        # Dict queue
        dict_queue: ChunkQueue[dict] = ChunkQueue()
        await dict_queue.put({"key": "value"})
        items = await dict_queue.get_available()
        assert items == [{"key": "value"}]
        await dict_queue.close()

        # Custom object queue
        class CustomObj:
            def __init__(self, val):
                self.val = val

            def __eq__(self, other):
                return self.val == other.val

        obj_queue: ChunkQueue[CustomObj] = ChunkQueue()
        obj = CustomObj(42)
        await obj_queue.put(obj)
        items = await obj_queue.get_available()
        assert len(items) == 1
        assert items[0].val == 42
        await obj_queue.close()
