"""WebSocket connection wrapper for control-plane streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import websockets
from websockets import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed

from luthien_proxy.proxy.websocket_logger import get_websocket_logger

logger = logging.getLogger(__name__)


@dataclass
class StreamConnection:
    """Persistent WebSocket connection for a single stream."""

    stream_id: str
    websocket: WebSocketClientProtocol
    outgoing_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    incoming_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    sender_task: Optional[asyncio.Task[None]] = None
    receiver_task: Optional[asyncio.Task[None]] = None
    error: Optional[BaseException] = None

    @classmethod
    async def create(
        cls,
        stream_id: str,
        control_plane_url: str,
        request_data: dict,
    ) -> StreamConnection:
        """Create and initialize a new WebSocket connection to the control plane.

        Args:
            stream_id: Unique identifier for this stream
            control_plane_url: Base URL of the control plane (http:// or https://)
            request_data: Request metadata to send in START message

        Returns:
            Initialized StreamConnection with background tasks running

        Raises:
            Exception: If WebSocket connection fails
        """
        websocket = await cls._open_websocket(control_plane_url, stream_id)
        connection = cls(stream_id=stream_id, websocket=websocket)
        connection.start()
        await connection.send({"type": "START", "data": request_data})
        return connection

    @staticmethod
    async def _open_websocket(control_plane_url: str, stream_id: str) -> WebSocketClientProtocol:
        """Open WebSocket connection to control plane for the given stream.

        Args:
            control_plane_url: Base URL (http:// or https://)
            stream_id: Stream identifier for URL path

        Returns:
            Connected WebSocket client

        Raises:
            Exception: If connection fails
        """
        ws_url = control_plane_url.rstrip("/")
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
        ws_logger = get_websocket_logger()
        while True:
            message = await self.outgoing_queue.get()
            if "_sentinel" in message:
                break
            try:
                # Log outgoing message before sending
                ws_logger.log_outgoing(self.stream_id, message)
                await self.websocket.send(json.dumps(message))
            except Exception as exc:  # pragma: no cover - network failure path
                self.error = exc
                logger.error("stream[%s] sender error: %s", self.stream_id, exc)
                break

    async def _receiver_loop(self) -> None:
        ws_logger = get_websocket_logger()
        try:
            async for raw in self.websocket:
                # raw may be str or bytes
                raw_text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw_text)
                    if not isinstance(payload, dict):
                        raise ValueError("control plane returned non-object message")
                    # Log incoming message after parsing
                    ws_logger.log_incoming(self.stream_id, payload)
                except Exception as exc:  # pragma: no cover - defensive
                    ws_logger.log_json_error(self.stream_id, raw_text, exc)
                    logger.error("stream[%s] invalid JSON from control plane: %s", self.stream_id, exc)
                    continue
                await self.incoming_queue.put(payload)
        except ConnectionClosed:
            pass
        except Exception as exc:  # pragma: no cover - network failure path
            self.error = exc
            logger.error("stream[%s] receiver error: %s", self.stream_id, exc)


__all__ = ["StreamConnection"]
