from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from litellm.types.utils import ModelResponseStream

from luthien_proxy.proxy.stream_orchestrator import (
    StreamOrchestrator,
    StreamTimeoutError,
)


class DummyConnection:
    """Test double that mimics the StreamConnection interface."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._incoming: asyncio.Queue[dict] = asyncio.Queue()
        self.error: BaseException | None = None

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def receive(self, timeout: float | None = None) -> dict | None:
        try:
            if timeout is None:
                return await self._incoming.get()
            return await asyncio.wait_for(self._incoming.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def queue_message(self, message: dict) -> None:
        self._incoming.put_nowait(message)


def make_stream_chunk(content: str) -> ModelResponseStream:
    return ModelResponseStream.model_validate(
        {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                }
            ],
        }
    )


async def collect(generator: AsyncIterator[ModelResponseStream]) -> list[ModelResponseStream]:
    return [item async for item in generator]


@pytest.mark.asyncio
async def test_orchestrator_yields_control_plane_chunks() -> None:
    connection = DummyConnection()
    upstream_chunk = make_stream_chunk("hi upstream")

    control_chunk = make_stream_chunk("hello from control")
    connection.queue_message({"type": "CHUNK", "data": control_chunk.model_dump()})
    connection.queue_message({"type": "END"})

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        yield upstream_chunk

    orchestrator = StreamOrchestrator(
        stream_id="stream-1",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=lambda data: ModelResponseStream.model_validate(data),
        timeout=0.5,
        chunk_logger=None,
        poll_interval=0.01,
    )

    received = await collect(orchestrator.run())

    assert [chunk.model_dump() for chunk in received] == [control_chunk.model_dump()]


@pytest.mark.asyncio
async def test_orchestrator_times_out_without_activity() -> None:
    connection = DummyConnection()
    upstream_chunk = make_stream_chunk("upstream")

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        yield upstream_chunk

    orchestrator = StreamOrchestrator(
        stream_id="timeout",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=lambda data: ModelResponseStream.model_validate(data),
        timeout=0.1,
        chunk_logger=None,
        poll_interval=0.01,
    )

    with pytest.raises(StreamTimeoutError):
        async for _ in orchestrator.run():
            pass


@pytest.mark.asyncio
async def test_keepalive_extends_deadline() -> None:
    connection = DummyConnection()
    upstream_chunk = make_stream_chunk("payload")
    control_chunk = make_stream_chunk("final")

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        yield upstream_chunk

    async def control_plane_messages() -> None:
        await asyncio.sleep(0.04)
        connection.queue_message({"type": "KEEPALIVE"})
        await asyncio.sleep(0.02)
        connection.queue_message({"type": "CHUNK", "data": control_chunk.model_dump()})
        connection.queue_message({"type": "END"})

    producer = asyncio.create_task(control_plane_messages())

    orchestrator = StreamOrchestrator(
        stream_id="keepalive",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=lambda data: ModelResponseStream.model_validate(data),
        timeout=0.05,
        chunk_logger=None,
        poll_interval=0.01,
    )

    received = await collect(orchestrator.run())
    await producer

    assert [chunk.model_dump() for chunk in received] == [control_chunk.model_dump()]
