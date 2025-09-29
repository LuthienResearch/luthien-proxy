"""WebSocket routes for streaming policy evaluation."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, Literal

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from luthien_proxy.control_plane.dependencies import get_active_policy
from luthien_proxy.policies.base import LuthienPolicy, StreamPolicyContext

logger = logging.getLogger(__name__)

router = APIRouter()

_active_streams: Dict[str, StreamPolicyContext] = {}
_StreamEnd = Literal["__STREAM_END__"]
STREAM_END: _StreamEnd = "__STREAM_END__"


class StreamProtocolError(Exception):
    """Raised when the incoming stream violates the expected protocol."""


def _interpret_stream_message(stream_id: str, message: dict[str, Any]) -> dict[str, Any] | None | _StreamEnd:
    """Return chunk payload or sentinel describing how to advance the loop."""
    msg_type = message.get("type")
    if msg_type == "CHUNK":
        data = message.get("data")
        if isinstance(data, dict):
            return data
        logger.warning("stream[%s] CHUNK missing data payload", stream_id)
        return None

    if msg_type == "END":
        return STREAM_END

    if msg_type == "ERROR":
        logger.error("stream[%s] client error: %s", stream_id, message.get("error"))
        return STREAM_END

    logger.warning("stream[%s] received unexpected message type %s", stream_id, msg_type)
    return None


async def _incoming_stream_from_websocket(
    websocket: WebSocket,
    stream_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yield chunks received from the websocket as an async iterator."""
    try:
        while True:
            message = await websocket.receive_json()
            outcome = _interpret_stream_message(stream_id, message)
            if outcome == STREAM_END:
                break
            if outcome is not None:
                yield outcome
    except WebSocketDisconnect:
        logger.info("stream[%s] client disconnected", stream_id)


async def _ensure_start_message(websocket: WebSocket) -> dict[str, Any]:
    """Validate and extract the START handshake payload."""
    message = await websocket.receive_json()
    if message.get("type") != "START":
        raise StreamProtocolError("Expected START message")

    data = message.get("data")
    return data if isinstance(data, dict) else {}


async def _forward_policy_output(
    websocket: WebSocket,
    policy: LuthienPolicy,
    context: StreamPolicyContext,
    incoming_stream: AsyncIterator[dict[str, Any]],
) -> None:
    """Send policy-generated chunks back over the websocket."""
    async for outgoing_chunk in policy.generate_response_stream(context, incoming_stream):
        await websocket.send_json({"type": "CHUNK", "data": outgoing_chunk})


async def _safe_send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    """Send JSON payload and ignore network failures."""
    try:
        await websocket.send_json(payload)
    except Exception:  # pragma: no cover - defensive
        pass


async def _safe_close(websocket: WebSocket) -> None:
    """Close the websocket, ignoring errors."""
    try:
        await websocket.close()
    except Exception:  # pragma: no cover - defensive
        pass


@router.websocket("/stream/{stream_id}")
async def policy_stream_endpoint(
    websocket: WebSocket,
    stream_id: str,
    policy: LuthienPolicy = Depends(get_active_policy),
) -> None:
    """Coordinate streaming requests between proxy and policies."""
    await websocket.accept()

    context: StreamPolicyContext | None = None

    try:
        request_data = await _ensure_start_message(websocket)
        context = policy.create_stream_context(stream_id, request_data)
        _active_streams[stream_id] = context

        incoming_stream = _incoming_stream_from_websocket(websocket, stream_id)
        await _forward_policy_output(websocket, policy, context, incoming_stream)
        await _safe_send_json(websocket, {"type": "END"})

    except StreamProtocolError as exc:
        logger.warning("stream[%s] protocol error: %s", stream_id, exc)
        await websocket.close(code=1002, reason=str(exc))
        return

    except WebSocketDisconnect:
        logger.info("stream[%s] disconnected during processing", stream_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("stream[%s] policy error: %s", stream_id, exc)
        await _safe_send_json(websocket, {"type": "ERROR", "error": str(exc)})
    finally:
        _active_streams.pop(stream_id, None)
        await _safe_close(websocket)


__all__ = ["router"]
