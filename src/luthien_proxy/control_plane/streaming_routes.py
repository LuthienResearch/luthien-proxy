"""WebSocket routes for streaming policy evaluation."""

from __future__ import annotations

import copy
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from luthien_proxy.control_plane.conversation.events import (
    build_conversation_events,
    clear_stream_indices,
    reset_stream_indices,
)
from luthien_proxy.control_plane.conversation.streams import (
    publish_conversation_event,
    publish_trace_conversation_event,
)
from luthien_proxy.control_plane.conversation.utils import json_safe
from luthien_proxy.control_plane.endpoint_logger import get_endpoint_logger
from luthien_proxy.control_plane.stream_context import StreamContextStore
from luthien_proxy.control_plane.utils.task_queue import (
    CONVERSATION_EVENT_QUEUE,
    DEBUG_LOG_QUEUE,
)
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
    on_chunk=None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield chunks received from the websocket as an async iterator."""
    endpoint_logger = get_endpoint_logger()
    chunk_index = 0

    try:
        while True:
            message = await websocket.receive_json()
            outcome = _interpret_stream_message(stream_id, message)
            if outcome == STREAM_END:
                break
            if outcome is not None:
                endpoint_logger.log_incoming_chunk(stream_id, outcome, chunk_index)
                chunk_index += 1

                if on_chunk is not None:
                    await on_chunk(copy.deepcopy(outcome))
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


async def _instrumented_incoming_stream(
    stream_id: str,
    policy_class_name: str,
    incoming_stream: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Wrap incoming stream with policy instrumentation logging."""
    from luthien_proxy.policies.policy_instrumentation import get_policy_logger

    policy_logger = get_policy_logger()
    chunk_in_index = 0

    async for chunk in incoming_stream:
        policy_logger.log_chunk_in(stream_id, policy_class_name, chunk, chunk_in_index)
        chunk_in_index += 1
        yield chunk


async def _forward_policy_output(
    websocket: WebSocket,
    policy: LuthienPolicy,
    context: StreamPolicyContext,
    incoming_stream: AsyncIterator[dict[str, Any]],
    on_chunk=None,
) -> None:
    """Send policy-generated chunks back over the websocket."""
    from luthien_proxy.policies.policy_instrumentation import get_policy_logger

    endpoint_logger = get_endpoint_logger()
    policy_logger = get_policy_logger()

    policy_class_name = policy.__class__.__name__
    policy_logger.log_stream_start(context.stream_id, policy_class_name)

    # Wrap incoming stream with instrumentation
    instrumented_incoming = _instrumented_incoming_stream(
        context.stream_id,
        policy_class_name,
        incoming_stream,
    )

    chunk_out_index = 0
    try:
        async for outgoing_chunk in policy.generate_response_stream(context, instrumented_incoming):
            policy_logger.log_chunk_out(context.stream_id, policy_class_name, outgoing_chunk, chunk_out_index)
            endpoint_logger.log_outgoing_chunk(context.stream_id, outgoing_chunk, chunk_out_index)
            chunk_out_index += 1

            await websocket.send_json({"type": "CHUNK", "data": outgoing_chunk})
            if on_chunk is not None:
                await on_chunk(copy.deepcopy(outgoing_chunk))
    finally:
        policy_logger.log_stream_end(context.stream_id, policy_class_name, chunk_out_index)


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


def _policy_from_websocket(websocket: WebSocket) -> LuthienPolicy:
    state = getattr(websocket.app, "state", None)
    if state is None:
        raise RuntimeError("WebSocket missing application state")
    policy = getattr(state, "active_policy", None)
    if policy is None:
        raise RuntimeError("Active policy not loaded for this app instance")
    return policy


def _redis_from_websocket(websocket: WebSocket):
    state = getattr(websocket.app, "state", None)
    if state is None:
        raise RuntimeError("WebSocket missing application state")
    client = getattr(state, "redis_client", None)
    if client is None:
        raise RuntimeError("Redis client not configured for this app instance")
    return client


def _hook_counters_from_websocket(websocket: WebSocket) -> Dict[str, int] | None:
    state = getattr(websocket.app, "state", None)
    if state is None:
        return None
    return getattr(state, "hook_counters", None)


def _debug_writer_from_websocket(websocket: WebSocket):
    state = getattr(websocket.app, "state", None)
    if state is None:
        return None
    return getattr(state, "debug_log_writer", None)


def _stream_store_from_websocket(websocket: WebSocket) -> StreamContextStore | None:
    state = getattr(websocket.app, "state", None)
    if state is None:
        return None
    return getattr(state, "stream_store", None)


class _StreamEventPublisher:
    """Helper to record debug logs and conversation events for streamed chunks."""

    hook_name = "async_post_call_streaming_iterator_hook"

    def __init__(self, websocket: WebSocket, request_data: dict[str, Any]):
        self._request_data = request_data
        self._redis = _redis_from_websocket(websocket)
        self._debug_writer = _debug_writer_from_websocket(websocket)
        self._hook_counters = _hook_counters_from_websocket(websocket)
        self._call_id = request_data.get("litellm_call_id")
        trace_id = request_data.get("litellm_trace_id")
        self._trace_id = trace_id if isinstance(trace_id, str) else None
        self._stream_store = _stream_store_from_websocket(websocket)
        self._pending_payload: dict[str, Any] | None = None
        self._original_text_parts: list[str] = []
        self._final_text_parts: list[str] = []
        if isinstance(self._call_id, str) and self._call_id:
            reset_stream_indices(self._call_id)

    async def record_original(self, chunk: dict[str, Any]) -> None:
        if self._hook_counters is not None:
            key = self.hook_name.lower()
            self._hook_counters[key] += 1

        payload = {
            "response": chunk,
            "request_data": self._request_data,
        }
        self._pending_payload = payload

        if self._debug_writer is not None:
            record: dict[str, Any] = {"hook": self.hook_name, "payload": payload}
            record["post_time_ns"] = time.time_ns()
            if self._trace_id:
                record["litellm_trace_id"] = self._trace_id
            if isinstance(self._call_id, str):
                record["litellm_call_id"] = self._call_id
            DEBUG_LOG_QUEUE.submit(self._debug_writer(f"hook:{self.hook_name}", record))

        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content")
            if isinstance(content, str):
                self._original_text_parts.append(content)

    async def record_result(self, chunk: dict[str, Any]) -> None:
        if self._pending_payload is None:
            self._pending_payload = {"response": {}, "request_data": self._request_data}

        result_payload = {
            "response": chunk,
            "request_data": self._request_data,
        }

        if self._debug_writer is not None:
            record = {
                "hook": self.hook_name,
                "litellm_call_id": self._call_id,
                "original": self._pending_payload,
                "result": json_safe(result_payload),
            }
            record["post_time_ns"] = time.time_ns()
            if self._trace_id:
                record["litellm_trace_id"] = self._trace_id
            DEBUG_LOG_QUEUE.submit(self._debug_writer(f"hook_result:{self.hook_name}", record))

        if isinstance(self._call_id, str) and self._call_id:
            events = build_conversation_events(
                hook=self.hook_name,
                call_id=self._call_id,
                trace_id=self._trace_id,
                original=self._pending_payload,
                result=json_safe(result_payload),
                timestamp_ns_fallback=time.time_ns(),
                timestamp=datetime.now(timezone.utc),
            )
            for event in events:
                CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(self._redis, event))
                CONVERSATION_EVENT_QUEUE.submit(publish_trace_conversation_event(self._redis, event))

        self._pending_payload = None

        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content")
            if not isinstance(content, str):
                continue
            self._final_text_parts.append(content)
            if self._stream_store is not None and isinstance(self._call_id, str):
                await self._stream_store.append_delta(self._call_id, content)

    async def finish(self) -> None:
        if isinstance(self._call_id, str) and self._call_id:
            original_text = "".join(self._original_text_parts).strip()
            final_text = "".join(self._final_text_parts)
            if self._stream_store is not None:
                try:
                    final_text = await self._stream_store.get_accumulated(self._call_id)
                except Exception:  # pragma: no cover - defensive
                    pass
            final_text = final_text.strip()
            if not final_text and original_text:
                final_text = original_text

            original_payload: dict[str, Any] = {
                "response": {"choices": [{"message": {"content": original_text}}]},
                "post_time_ns": time.time_ns(),
            }
            summary_payload: dict[str, Any] = {
                "response": {"choices": [{"message": {"content": final_text}}]},
                "post_time_ns": time.time_ns(),
            }

            if self._debug_writer is not None:
                record = {
                    "hook": "async_post_call_streaming_hook",
                    "litellm_call_id": self._call_id,
                    "original": original_payload,
                    "result": summary_payload,
                }
                record["post_time_ns"] = time.time_ns()
                if self._trace_id:
                    record["litellm_trace_id"] = self._trace_id
                DEBUG_LOG_QUEUE.submit(
                    self._debug_writer(
                        "hook_result:async_post_call_streaming_hook",
                        record,
                    )
                )

            events = build_conversation_events(
                hook="async_post_call_streaming_hook",
                call_id=self._call_id,
                trace_id=self._trace_id,
                original=original_payload,
                result=summary_payload,
                timestamp_ns_fallback=time.time_ns(),
                timestamp=datetime.now(timezone.utc),
            )
            for event in events:
                CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(self._redis, event))
                CONVERSATION_EVENT_QUEUE.submit(publish_trace_conversation_event(self._redis, event))

            clear_stream_indices(self._call_id)
            if self._stream_store is not None:
                await self._stream_store.clear(self._call_id)


@router.websocket("/stream/{stream_id}")
async def policy_stream_endpoint(
    websocket: WebSocket,
    stream_id: str,
) -> None:
    """Coordinate streaming requests between proxy and policies."""
    await websocket.accept()

    policy = _policy_from_websocket(websocket)
    context: StreamPolicyContext | None = None
    endpoint_logger = get_endpoint_logger()

    try:
        request_data = await _ensure_start_message(websocket)
        endpoint_logger.log_start_message(stream_id, request_data)

        publisher = _StreamEventPublisher(websocket, request_data)

        context = policy.create_stream_context(stream_id, request_data)
        _active_streams[stream_id] = context

        endpoint_logger.log_policy_invocation(stream_id, policy.__class__.__name__, request_data)

        incoming_stream = _incoming_stream_from_websocket(
            websocket,
            stream_id,
            on_chunk=publisher.record_original,
        )
        await _forward_policy_output(
            websocket,
            policy,
            context,
            incoming_stream,
            on_chunk=publisher.record_result,
        )
        await publisher.finish()
        await _safe_send_json(websocket, {"type": "END"})
        endpoint_logger.log_end_message(stream_id)

    except StreamProtocolError as exc:
        logger.warning("stream[%s] protocol error: %s", stream_id, exc)
        endpoint_logger.log_error(stream_id, f"Protocol error: {exc}")
        await websocket.close(code=1002, reason=str(exc))
        return

    except WebSocketDisconnect:
        logger.info("stream[%s] disconnected during processing", stream_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("stream[%s] policy error: %s", stream_id, exc)
        endpoint_logger.log_error(stream_id, str(exc))
        await _safe_send_json(websocket, {"type": "ERROR", "error": str(exc)})
    finally:
        _active_streams.pop(stream_id, None)
        await _safe_close(websocket)


__all__ = ["router"]
