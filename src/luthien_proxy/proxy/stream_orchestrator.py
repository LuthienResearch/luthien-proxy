"""Bidirectional streaming orchestrator between LiteLLM and the control plane.

The orchestrator coordinates upstream forwarding, control-plane polling, and
timeout enforcement.

Protocol Specification
----------------------

Messages to Control Plane (via StreamConnection.send):
  * ``{"type": "CHUNK", "data": <ModelResponseStream dict>}``: Forward upstream chunk
  * ``{"type": "END"}``: Signal upstream completion

Messages from Control Plane (via StreamConnection.receive):
  * ``{"type": "CHUNK", "data": <dict>}``: Control plane chunk to yield to client.
    The ``data`` dict will be normalized back to ``ModelResponseStream`` via the
    normalize_chunk callback.
  * ``{"type": "KEEPALIVE"}``: Extends the activity deadline without emitting chunks.
    Used to prevent timeouts during slow policy processing.
  * ``{"type": "END"}``: Signals completion and stops iteration. No more chunks
    will be yielded.
  * ``{"type": "ERROR", "message": <str>}``: Aborts the stream with a
    ``StreamProtocolError``.

State Machine
-------------
  ACTIVE → ENDED: Normal completion (upstream finished or control plane sent END)
  ACTIVE → FAILED: Error condition (timeout, connection error, protocol violation)

The orchestrator enforces an activity-based timeout that resets on ANY message
from the control plane (including KEEPALIVE). Connection shutdown remains the
caller's responsibility; the orchestrator only sends control-plane ``END``
notifications.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, cast

from litellm.types.utils import ModelResponseStream

from luthien_proxy.proxy.callback_chunk_logger import CallbackChunkLogger
from luthien_proxy.proxy.stream_connection_manager import StreamConnection
from luthien_proxy.utils.constants import MIN_STREAM_POLL_INTERVAL_SECONDS

logger = logging.getLogger(__name__)


class StreamState(Enum):
    """Lifecycle states for a bidirectional stream."""

    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class StreamOrchestrationError(RuntimeError):
    """Base exception for orchestration failures."""


class StreamTimeoutError(StreamOrchestrationError, TimeoutError):
    """Raised when no control-plane activity occurs within the timeout window."""


class StreamProtocolError(StreamOrchestrationError):
    """Raised when control-plane messages violate the expected protocol."""


class StreamConnectionError(StreamOrchestrationError):
    """Raised when the underlying WebSocket connection fails."""


class StreamOrchestrator:
    """Manage bidirectional streaming between upstream and the control plane.

    Args:
        stream_id: Identifier used for logging and correlation.
        connection: Active WebSocket connection to the control plane. Callers are
            responsible for closing the connection after ``run`` completes.
        upstream: Async iterator yielding chunks from the upstream model.
        normalize_chunk: Callable converting control-plane payloads into
            ``ModelResponseStream`` instances.
        timeout: Maximum idle period (seconds) before the stream fails.
        chunk_logger: Optional tracer for debugging the control-plane loop.
        poll_interval: Maximum interval between control-plane polls. Lower values
            reduce timeout jitter but increase wakeups.
        clock: Injectable monotonic clock used for deterministic testing.
    """

    def __init__(
        self,
        *,
        stream_id: str,
        connection: StreamConnection,
        upstream: AsyncIterator[ModelResponseStream] | AsyncGenerator[ModelResponseStream, None],
        normalize_chunk: Callable[[dict[str, Any]], ModelResponseStream],
        timeout: float,
        chunk_logger: CallbackChunkLogger | None = None,
        poll_interval: float = 0.1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Initialize orchestrator state and timers."""
        self._stream_id = stream_id
        self._connection = connection
        self._upstream = upstream
        self._normalize_chunk = normalize_chunk
        self._timeout = timeout
        self._chunk_logger = chunk_logger
        self._poll_interval = max(MIN_STREAM_POLL_INTERVAL_SECONDS, poll_interval)
        self._clock = clock or time.monotonic

        now = self._clock()
        self._state = StreamState.ACTIVE
        self._last_activity = now
        self._deadline = now + timeout

        self._control_chunk_index = 0
        self._client_chunk_index = 0

        self._failure_exc: BaseException | None = None
        self._sent_end = False

    @property
    def state(self) -> StreamState:
        """Report the current lifecycle state."""
        return self._state

    async def run(self) -> AsyncGenerator[ModelResponseStream, None]:
        """Coordinate upstream forwarding and control-plane responses.

        Yields normalized chunks until the control plane signals ``END`` or an
        error occurs. Raises ``StreamTimeoutError``, ``StreamProtocolError`` or
        ``StreamConnectionError`` to surface the corresponding failure modes.
        """
        forward_task = asyncio.create_task(self._forward_upstream(), name=f"forward-{self._stream_id}")

        try:
            async for chunk in self._poll_control_plane():
                yield chunk
                if self._state != StreamState.ACTIVE:
                    break
        finally:
            forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await forward_task

            if self._state == StreamState.FAILED and self._failure_exc is not None:
                if isinstance(self._failure_exc, StreamOrchestrationError):
                    raise self._failure_exc
                raise StreamConnectionError("stream failed") from self._failure_exc

    async def _forward_upstream(self) -> None:
        """Forward upstream chunks to the control plane in the background."""
        try:
            async for chunk in self._upstream:
                if self._state != StreamState.ACTIVE:
                    break

                try:
                    await self._connection.send({"type": "CHUNK", "data": chunk.model_dump()})
                except Exception as exc:  # pragma: no cover - network failure path
                    self._fail(
                        StreamConnectionError("failed to forward upstream chunk"),
                        cause=exc,
                    )
                    logger.error(f"stream[{self._stream_id}] failed to forward upstream chunk: {exc}")
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(StreamConnectionError("upstream iteration failed"), cause=exc)
            logger.error(f"stream[{self._stream_id}] upstream iteration failed: {exc}")
        finally:
            try:
                if not self._sent_end and self._state != StreamState.FAILED:
                    await self._connection.send({"type": "END"})
                    self._sent_end = True
            except Exception as exc:  # pragma: no cover - network failure path
                self._fail(
                    StreamConnectionError("failed to forward upstream END"),
                    cause=exc,
                )
                logger.error(f"stream[{self._stream_id}] failed to forward upstream END: {exc}")

            aclose = getattr(self._upstream, "aclose", None)
            if callable(aclose):
                close_callable = cast(Callable[[], Awaitable[None]], aclose)
                with contextlib.suppress(Exception):  # pragma: no cover - best-effort cleanup
                    await close_callable()

    async def _poll_control_plane(self) -> AsyncGenerator[ModelResponseStream, None]:
        """Yield control-plane chunks while enforcing the activity timeout."""
        while True:
            if self._state == StreamState.FAILED:
                if self._failure_exc is not None:
                    raise self._failure_exc
                raise StreamConnectionError("stream failed without explicit reason")

            if self._state == StreamState.ENDED:
                break

            now = self._clock()
            remaining = self._deadline - now
            if remaining <= 0:
                timeout_exc = StreamTimeoutError(f"no control-plane activity for {self._timeout:.2f}s")
                self._fail(timeout_exc)
                raise timeout_exc

            try:
                message = await self._connection.receive(timeout=min(self._poll_interval, remaining))
            except Exception as exc:  # pragma: no cover - network failure path
                self._fail(
                    StreamConnectionError("failed to receive from control plane"),
                    cause=exc,
                )
                logger.error(f"stream[{self._stream_id}] failed to receive from control plane: {exc}")
                failure = self._failure_exc or StreamConnectionError("control plane receive failed")
                raise failure

            if message is None:
                # Timeout occurred - continue polling
                continue

            if self._chunk_logger is not None:
                self._chunk_logger.log_control_chunk_received(self._stream_id, message, self._control_chunk_index)
            self._control_chunk_index += 1

            msg_type = message.get("type")

            if msg_type == "CHUNK":
                chunk_payload_raw = message.get("data")
                if not isinstance(chunk_payload_raw, dict):
                    exc = StreamProtocolError("control plane returned non-dict chunk")
                    self._fail(exc)
                    raise exc
                chunk_payload: dict[str, Any] = chunk_payload_raw
                try:
                    normalized = self._normalize_chunk(chunk_payload)
                except Exception as exc:
                    if self._chunk_logger is not None:
                        self._chunk_logger.log_chunk_normalized(
                            self._stream_id,
                            chunk_payload,
                            success=False,
                            error=str(exc),
                        )
                    protocol_exc = StreamProtocolError("control plane returned invalid chunk")
                    self._fail(protocol_exc, cause=exc)
                    raise protocol_exc

                if not isinstance(normalized, ModelResponseStream):
                    error_msg = f"normalize_chunk must return ModelResponseStream, got {type(normalized).__name__}"
                    if self._chunk_logger is not None:
                        self._chunk_logger.log_chunk_normalized(
                            self._stream_id,
                            chunk_payload,
                            success=False,
                            error=error_msg,
                        )
                    protocol_exc = StreamProtocolError(error_msg)
                    self._fail(protocol_exc)
                    raise protocol_exc

                if self._chunk_logger is not None:
                    self._chunk_logger.log_chunk_normalized(
                        self._stream_id,
                        normalized.model_dump(),
                        success=True,
                    )

                self._register_activity()

                if self._chunk_logger is not None:
                    self._chunk_logger.log_chunk_to_client(
                        self._stream_id,
                        normalized.model_dump(),
                        self._client_chunk_index,
                    )
                self._client_chunk_index += 1

                yield normalized
                continue

            if msg_type == "KEEPALIVE":
                self._register_activity()
                logger.debug(f"stream[{self._stream_id}] received KEEPALIVE")
                continue

            if msg_type == "END":
                logger.debug(f"stream[{self._stream_id}] received END")
                self._state = StreamState.ENDED
                break

            if msg_type == "ERROR":
                error_message = message.get("error", "unknown control plane error")
                exc = StreamProtocolError(f"control plane error: {error_message}")
                self._fail(exc)
                raise exc

            logger.warning(f"stream[{self._stream_id}] unexpected control-plane message type {msg_type!r}")

        return

    def _register_activity(self) -> None:
        now = self._clock()
        self._last_activity = now
        self._deadline = now + self._timeout

    def _fail(self, exc: BaseException, *, cause: BaseException | None = None) -> None:
        if self._state == StreamState.FAILED:
            return
        if cause is not None:
            try:
                exc.__cause__ = cause
            except Exception:  # pragma: no cover - defensive
                pass
        self._state = StreamState.FAILED
        self._failure_exc = exc


__all__ = [
    "StreamConnectionError",
    "StreamOrchestrationError",
    "StreamOrchestrator",
    "StreamProtocolError",
    "StreamState",
    "StreamTimeoutError",
]
