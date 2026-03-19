"""Unit tests for in-process event publisher."""

import asyncio
import json

import pytest

from luthien_proxy.observability.event_publisher import InProcessEventPublisher


class TestInProcessEventPublisher:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        publisher = InProcessEventPublisher()
        received: list[str] = []

        async def consume():
            async for event in publisher.stream_events(heartbeat_seconds=999):
                received.append(event)
                break  # stop after first event

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)  # let consumer register

        await publisher.publish_event("call-1", "test.event", {"key": "value"})
        await asyncio.wait_for(consumer_task, timeout=1.0)

        assert len(received) == 1
        assert "call-1" in received[0]
        assert "test.event" in received[0]

    @pytest.mark.asyncio
    async def test_publish_delivers_to_multiple_subscribers(self):
        publisher = InProcessEventPublisher()
        received_a: list[str] = []
        received_b: list[str] = []

        async def consume(target: list[str]):
            async for event in publisher.stream_events(heartbeat_seconds=999):
                target.append(event)
                break

        task_a = asyncio.create_task(consume(received_a))
        task_b = asyncio.create_task(consume(received_b))
        await asyncio.sleep(0.01)

        await publisher.publish_event("call-1", "test.event")
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)

        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_no_subscribers_does_not_error(self):
        publisher = InProcessEventPublisher()
        await publisher.publish_event("call-1", "test.event")  # should not raise

    @pytest.mark.asyncio
    async def test_cancelled_subscriber_does_not_receive_events(self):
        """After cancellation, new events are not delivered to the cancelled consumer."""
        publisher = InProcessEventPublisher()
        received: list[str] = []

        async def consume():
            async for event in publisher.stream_events(heartbeat_seconds=999):
                received.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        # Deliver one event, then cancel
        await publisher.publish_event("call-1", "before.cancel")
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Publish after cancel — should not raise, should not deliver
        await publisher.publish_event("call-2", "after.cancel")
        assert len(received) == 1
        assert "before.cancel" in received[0]

    @pytest.mark.asyncio
    async def test_stream_events_produces_sse_format(self):
        publisher = InProcessEventPublisher()
        received: list[str] = []

        async def consume():
            async for event in publisher.stream_events(heartbeat_seconds=999):
                received.append(event)
                break

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        await publisher.publish_event("call-1", "test.event")
        await asyncio.wait_for(task, timeout=1.0)

        assert received[0].startswith("data: ")
        assert received[0].endswith("\n\n")
        payload = json.loads(received[0].removeprefix("data: ").strip())
        assert payload["call_id"] == "call-1"
        assert payload["event_type"] == "test.event"
