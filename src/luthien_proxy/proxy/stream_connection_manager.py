"""Utilities for managing persistent control-plane streaming connections."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

import websockets
from websockets import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


@dataclass
class StreamConnection:
    """Persistent WebSocket connection for a single stream."""

    stream_id: str
    websocket: WebSocketClientProtocol
    outgoing_queue: asyncio.Queue[dict] = field(default_factory=asyncio.Queue)
    incoming_queue: asyncio.Queue[dict] = field(default_factory=asyncio.Queue)
    sender_task: Optional[asyncio.Task[None]] = None
    receiver_task: Optional[asyncio.Task[None]] = None
    error: Optional[BaseException] = None

    async def send(self, message: dict) -> None:
        """Enqueue a message to send to the control plane."""
        await self.outgoing_queue.put(message)

    async def receive(self, timeout: float | None = None) -> Optional[dict]:
        """Return the next message produced by the control plane."""
        try:
            if timeout is None:
                return await self.incoming_queue.get()
            return await asyncio.wait_for(self.incoming_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def start(self) -> None:
        """Start background sender/receiver tasks."""
        if self.sender_task is None:
            self.sender_task = asyncio.create_task(self._sender_loop())
        if self.receiver_task is None:
            self.receiver_task = asyncio.create_task(self._receiver_loop())

    async def close(self) -> None:
        """Close connection and cancel background tasks."""
        await self.outgoing_queue.put({"_sentinel": True})

        if self.sender_task is not None:
            try:
                await asyncio.wait_for(self.sender_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.sender_task.cancel()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Sender task finished with error: %s", exc)

        try:
            await self.websocket.close()
        except Exception:
            pass

        if self.receiver_task is not None:
            try:
                await asyncio.wait_for(self.receiver_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.receiver_task.cancel()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Receiver task finished with error: %s", exc)

    async def _sender_loop(self) -> None:
        while True:
            message = await self.outgoing_queue.get()
            if "_sentinel" in message:
                break
            try:
                await self.websocket.send(json.dumps(message))
            except Exception as exc:  # pragma: no cover - network failure path
                self.error = exc
                logger.error("stream[%s] sender error: %s", self.stream_id, exc)
                break

    async def _receiver_loop(self) -> None:
        try:
            async for raw in self.websocket:
                try:
                    payload = json.loads(raw)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.error("stream[%s] invalid JSON from control plane: %s", self.stream_id, exc)
                    continue
                await self.incoming_queue.put(payload)
        except ConnectionClosed:
            pass
        except Exception as exc:  # pragma: no cover - network failure path
            self.error = exc
            logger.error("stream[%s] receiver error: %s", self.stream_id, exc)


class StreamConnectionManager:
    """Create and cache WebSocket connections for streams."""

    def __init__(self, control_plane_url: str) -> None:
        """Configure the manager with the control-plane endpoint."""
        self._control_plane_url = control_plane_url.rstrip("/")
        self._connections: Dict[str, StreamConnection] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, stream_id: str, request_data: dict) -> StreamConnection:
        """Return an active connection for the supplied stream id."""
        async with self._lock:
            existing = self._connections.get(stream_id)
            if existing and existing.error is None:
                return existing

            if existing is not None:
                await existing.close()

            websocket = await self._open_websocket(stream_id)
            connection = StreamConnection(stream_id=stream_id, websocket=websocket)
            connection.start()
            await connection.send({"type": "START", "data": request_data})

            self._connections[stream_id] = connection
            return connection

    async def lookup(self, stream_id: str) -> Optional[StreamConnection]:
        """Return the active connection for stream_id if present."""
        async with self._lock:
            return self._connections.get(stream_id)

    async def close(self, stream_id: str) -> None:
        """Close and forget the cached connection for stream_id."""
        async with self._lock:
            connection = self._connections.pop(stream_id, None)
            if connection is not None:
                await connection.close()

    async def close_all(self) -> None:
        """Close every cached connection."""
        async with self._lock:
            connections = list(self._connections.items())
            self._connections.clear()
        for _, connection in connections:
            await connection.close()

    async def _open_websocket(self, stream_id: str) -> WebSocketClientProtocol:
        ws_url = self._control_plane_url
        if ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[len("http://") :]
        elif ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[len("https://") :]
        ws_url = f"{ws_url}/stream/{stream_id}"

        try:
            return await websockets.connect(ws_url)
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("failed to connect to control plane for stream %s: %s", stream_id, exc)
            raise


__all__ = ["StreamConnection", "StreamConnectionManager"]
