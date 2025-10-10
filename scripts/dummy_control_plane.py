"""Minimal control-plane stub for manual testing.

This ASGI app mirrors the HTTP + WebSocket surface that the proxy expects. It
simply echoes whatever the proxy sends so we can validate the proxy behaviour
without running the real control plane.

Usage::

    uv run uvicorn scripts.dummy_control_plane:app --reload --host 0.0.0.0 --port 8081

The proxy can then point ``CONTROL_PLANE_URL`` at ``http://localhost:8081``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

app = FastAPI()


@app.post("/api/hooks/{hook}")
async def echo_hook(hook: str, request: Request) -> Any:
    """Echo back whatever payload the proxy posts."""

    payload = await request.json()
    logger.info("hook[%s] received payload size=%d", hook, len(json.dumps(payload)))
    return payload


@app.get("/health")
async def health() -> dict[str, str]:
    """Simple readiness endpoint."""

    return {"status": "ok"}


@app.websocket("/stream/{stream_id}")
async def echo_stream(websocket: WebSocket, stream_id: str) -> None:
    """Round-trip streaming messages without modification."""

    await websocket.accept()
    logger.info("stream[%s] connection established", stream_id)

    try:
        while True:
            message = await websocket.receive()
            if "text" in message:
                raw = message["text"]
            elif "bytes" in message and message["bytes"] is not None:
                raw = message["bytes"].decode("utf-8")
            else:
                # Unknown frame; ignore
                continue

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("stream[%s] received invalid JSON: %s", stream_id, raw)
                continue

            msg_type = payload.get("type")
            if msg_type == "START":
                # No-op: proxy will start forwarding upstream chunks next.
                continue
            if msg_type == "CHUNK":
                await websocket.send_text(raw)
                continue
            if msg_type == "END":
                await websocket.send_text(json.dumps({"type": "END"}))
                break

            # Unknown message types are ignored to keep the stub simple.
            logger.info("stream[%s] ignoring message type=%s", stream_id, msg_type)

    except WebSocketDisconnect:
        logger.info("stream[%s] disconnected", stream_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - diagnostic
        logger.error("stream[%s] unexpected error: %s", stream_id, exc)
    finally:
        await websocket.close()
        logger.info("stream[%s] closed", stream_id)
