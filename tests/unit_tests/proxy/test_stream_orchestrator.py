from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

import pytest
from litellm.types.utils import ModelResponseStream

from luthien_proxy.proxy.stream_orchestrator import (
    StreamConnectionError,
    StreamOrchestrator,
    StreamProtocolError,
    StreamTimeoutError,
)
from luthien_proxy.utils.constants import MIN_STREAM_POLL_INTERVAL_SECONDS


class DummyConnection:
    """Test double that mimics the StreamConnection interface."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._incoming: asyncio.Queue[dict] = asyncio.Queue()
        self.send_error: BaseException | None = None
        self.receive_error: BaseException | None = None

    async def send(self, message: dict) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)

    async def receive(self, timeout: float | None = None) -> dict | None:
        if self.receive_error is not None:
            raise self.receive_error
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
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
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
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
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
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
    )

    received = await collect(orchestrator.run())
    await producer

    assert [chunk.model_dump() for chunk in received] == [control_chunk.model_dump()]


@pytest.mark.asyncio
async def test_control_plane_non_dict_chunk_raises_protocol_error() -> None:
    connection = DummyConnection()
    connection.queue_message({"type": "CHUNK", "data": "oops"})

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        yield make_stream_chunk("upstream")

    orchestrator = StreamOrchestrator(
        stream_id="protocol-nondict",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=lambda data: ModelResponseStream.model_validate(data),
        timeout=0.1,
        chunk_logger=None,
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
    )

    with pytest.raises(StreamProtocolError):
        async for _ in orchestrator.run():
            pass


@pytest.mark.asyncio
async def test_normalize_failure_raises_protocol_error() -> None:
    connection = DummyConnection()
    connection.queue_message({"type": "CHUNK", "data": {"foo": "bar"}})

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        yield make_stream_chunk("upstream")

    def normalize(_: dict) -> ModelResponseStream:
        raise ValueError("bad payload")

    orchestrator = StreamOrchestrator(
        stream_id="protocol-normalize",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=normalize,
        timeout=0.1,
        chunk_logger=None,
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
    )

    with pytest.raises(StreamProtocolError):
        async for _ in orchestrator.run():
            pass


@pytest.mark.asyncio
async def test_normalize_must_return_model_response_stream() -> None:
    connection = DummyConnection()
    control_chunk = make_stream_chunk("control")
    connection.queue_message({"type": "CHUNK", "data": control_chunk.model_dump()})

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        yield make_stream_chunk("upstream")

    def normalize(_: dict) -> ModelResponseStream:
        return cast(ModelResponseStream, {"not": "a-model"})

    orchestrator = StreamOrchestrator(
        stream_id="protocol-normalize-type",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=normalize,
        timeout=0.1,
        chunk_logger=None,
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
    )

    with pytest.raises(StreamProtocolError):
        async for _ in orchestrator.run():
            pass


@pytest.mark.asyncio
async def test_receive_failure_raises_connection_error() -> None:
    connection = DummyConnection()
    connection.receive_error = RuntimeError("recv boom")

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        yield make_stream_chunk("ignored")

    orchestrator = StreamOrchestrator(
        stream_id="receive-error",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=lambda data: ModelResponseStream.model_validate(data),
        timeout=0.1,
        chunk_logger=None,
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
    )

    with pytest.raises(StreamConnectionError):
        async for _ in orchestrator.run():
            pass


@pytest.mark.asyncio
async def test_upstream_failure_surfaces_as_connection_error() -> None:
    connection = DummyConnection()

    async def upstream() -> AsyncIterator[ModelResponseStream]:
        raise RuntimeError("upstream boom")
        yield make_stream_chunk("unreachable")

    orchestrator = StreamOrchestrator(
        stream_id="upstream-error",
        connection=connection,
        upstream=upstream(),
        normalize_chunk=lambda data: ModelResponseStream.model_validate(data),
        timeout=0.1,
        chunk_logger=None,
        poll_interval=MIN_STREAM_POLL_INTERVAL_SECONDS,
    )

    with pytest.raises(StreamConnectionError):
        async for _ in orchestrator.run():
            pass
