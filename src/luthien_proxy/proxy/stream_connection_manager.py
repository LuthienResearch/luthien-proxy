"""WebSocket connection wrapper for control-plane streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import websockets
from websockets import WebSocketClientProtocol

from luthien_proxy.proxy.websocket_logger import get_websocket_logger

logger = logging.getLogger(__name__)


class StreamConnection:
    """Thin wrapper around WebSocket for JSON message exchange."""

    def __init__(self, stream_id: str, websocket: WebSocketClientProtocol) -> None:
        """Initialize connection with WebSocket.

        Args:
            stream_id: Unique identifier for this stream
            websocket: Connected WebSocket client
        """
        self.stream_id = stream_id
        self._ws = websocket
        self._ws_logger = get_websocket_logger()
        self._closed = False

    @classmethod
    async def create(
        cls,
        stream_id: str,
        control_plane_url: str,
    ) -> StreamConnection:
        """Create and open a new WebSocket connection to the control plane.

        Args:
            stream_id: Unique identifier for this stream
            control_plane_url: Base URL of the control plane (http:// or https://)

        Returns:
            Connected StreamConnection instance

        Raises:
            Exception: If WebSocket connection fails
        """
        ws_url = control_plane_url.rstrip("/")
        if ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[len("http://") :]
        elif ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[len("https://") :]
        ws_url = f"{ws_url}/stream/{stream_id}"

        try:
            websocket = await websockets.connect(ws_url)
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error(f"failed to connect to control plane for stream {stream_id}: {exc}")
            raise

        return cls(stream_id=stream_id, websocket=websocket)

    async def send(self, message: dict) -> None:
        """Send JSON message to control plane.

        Args:
            message: Dictionary to send as JSON

        Raises:
            Exception: If send fails
        """
        self._ws_logger.log_outgoing(self.stream_id, message)
        await self._ws.send(json.dumps(message))

    async def receive(self, timeout: float | None = None) -> Optional[dict]:
        """Receive JSON message from control plane.

        Args:
            timeout: Optional timeout in seconds. Returns None on timeout.

        Returns:
            Parsed JSON dict, or None if timeout occurred

        Raises:
            Exception: If receive fails (connection closed, invalid JSON, etc.)
        """
        try:
            if timeout is not None:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            else:
                raw = await self._ws.recv()
        except asyncio.TimeoutError:
            return None

        # Parse JSON
        raw_text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                raise ValueError("control plane returned non-object message")
            self._ws_logger.log_incoming(self.stream_id, payload)
            return payload
        except Exception as exc:
            self._ws_logger.log_json_error(self.stream_id, raw_text, exc)
            logger.error(f"stream[{self.stream_id}] invalid JSON from control plane: {exc}")
            raise

    async def close(self) -> None:
        """Close the WebSocket connection.

        Safe to call multiple times; subsequent calls are ignored.
        """
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"stream[{self.stream_id}] error closing WebSocket: {exc}")
            pass


__all__ = ["StreamConnection"]
