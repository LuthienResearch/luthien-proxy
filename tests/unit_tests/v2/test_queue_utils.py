# ABOUTME: Unit tests for queue utilities
# ABOUTME: Tests get_available() batch processing function

"""Tests for queue utilities."""

import asyncio

import pytest

from luthien_proxy.v2.control.queue_utils import get_available


class TestGetAvailable:
    """Test get_available queue utility."""

    @pytest.mark.asyncio
    async def test_get_single_item(self):
        """Test getting single available item."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        await queue.put("item1")

        result = await get_available(queue)

        assert result == ["item1"]

    @pytest.mark.asyncio
    async def test_get_multiple_items(self):
        """Test getting multiple available items in batch."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        await queue.put("item1")
        await queue.put("item2")
        await queue.put("item3")

        result = await get_available(queue)

        assert result == ["item1", "item2", "item3"]

    @pytest.mark.asyncio
    async def test_empty_queue_after_shutdown(self):
        """Test that shutdown queue returns empty list."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        queue.shutdown()

        result = await get_available(queue)

        assert result == []

    @pytest.mark.asyncio
    async def test_partial_batch_before_shutdown(self):
        """Test getting partial batch when queue shuts down mid-drain."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        await queue.put("item1")
        await queue.put("item2")
        queue.shutdown()

        result = await get_available(queue)

        assert result == ["item1", "item2"]

    @pytest.mark.asyncio
    async def test_blocks_until_item_available(self):
        """Test that get_available blocks until item is ready."""
        queue: asyncio.Queue[str] = asyncio.Queue()

        # Schedule item to be added after short delay
        async def delayed_put():
            await asyncio.sleep(0.01)
            await queue.put("delayed_item")

        put_task = asyncio.create_task(delayed_put())
        result = await get_available(queue)
        await put_task

        assert result == ["delayed_item"]

    @pytest.mark.asyncio
    async def test_drains_queue_only_once(self):
        """Test that get_available only drains currently available items."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        await queue.put("batch1-item1")
        await queue.put("batch1-item2")

        # Get first batch
        batch1 = await get_available(queue)

        # Add more items
        await queue.put("batch2-item1")

        # Get second batch
        batch2 = await get_available(queue)

        assert batch1 == ["batch1-item1", "batch1-item2"]
        assert batch2 == ["batch2-item1"]

    @pytest.mark.asyncio
    async def test_with_concurrent_producer(self):
        """Test get_available with concurrent producer."""
        queue: asyncio.Queue[int] = asyncio.Queue()

        async def producer():
            for i in range(10):
                await queue.put(i)
                await asyncio.sleep(0.001)
            queue.shutdown()

        # Start producer
        producer_task = asyncio.create_task(producer())

        # Consume in batches
        all_items = []
        while True:
            batch = await get_available(queue)
            if not batch:
                break
            all_items.extend(batch)

        await producer_task

        # Should have all items
        assert all_items == list(range(10))

    @pytest.mark.asyncio
    async def test_preserves_order(self):
        """Test that get_available preserves insertion order."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        items = ["first", "second", "third", "fourth", "fifth"]

        for item in items:
            await queue.put(item)

        result = await get_available(queue)

        assert result == items

    @pytest.mark.asyncio
    async def test_handles_mixed_types(self):
        """Test get_available works with different types."""
        queue: asyncio.Queue[int | str] = asyncio.Queue()
        await queue.put(42)
        await queue.put("hello")
        await queue.put(3.14)

        result = await get_available(queue)

        assert result == [42, "hello", 3.14]
