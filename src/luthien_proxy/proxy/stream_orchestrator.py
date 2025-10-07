"""Bidirectional streaming orchestrator between LiteLLM and the control plane."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from enum import Enum
from typing import Any, Optional

from litellm.types.utils import ModelResponseStream

from luthien_proxy.proxy.callback_chunk_logger import CallbackChunkLogger
from luthien_proxy.proxy.stream_connection_manager import StreamConnection

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
    """Manage bidirectional streaming between upstream and the control plane."""

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
        """Initialize orchestrator with upstream source and control plane connection."""
        self._stream_id = stream_id
        self._connection = connection
        self._upstream = upstream
        self._normalize_chunk = normalize_chunk
        self._timeout = timeout
        self._chunk_logger = chunk_logger
        self._poll_interval = max(0.01, poll_interval)
        self._clock = clock or time.monotonic

        now = self._clock()
        self._state = StreamState.ACTIVE
        self._last_activity = now
        self._deadline = now + timeout

        self._control_chunk_index = 0
        self._client_chunk_index = 0

        self._failure_exc: Optional[BaseException] = None
        self._sent_end = False

    @property
    def state(self) -> StreamState:
        """Report the current lifecycle state."""
        return self._state

    async def run(self) -> AsyncGenerator[ModelResponseStream, None]:
        """Coordinate upstream forwarding and control-plane responses."""
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
                    logger.error("stream[%s] failed to forward upstream chunk: %s", self._stream_id, exc)
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(StreamConnectionError("upstream iteration failed"), cause=exc)
            logger.error("stream[%s] upstream iteration failed: %s", self._stream_id, exc)
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
                logger.error("stream[%s] failed to forward upstream END: %s", self._stream_id, exc)

            if isinstance(self._upstream, AsyncGenerator):
                with contextlib.suppress(Exception):  # pragma: no cover - best-effort cleanup
                    await self._upstream.aclose()

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
                logger.error("stream[%s] failed to receive from control plane: %s", self._stream_id, exc)
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
                logger.debug("stream[%s] received KEEPALIVE", self._stream_id)
                continue

            if msg_type == "END":
                logger.debug("stream[%s] received END", self._stream_id)
                self._state = StreamState.ENDED
                break

            if msg_type == "ERROR":
                error_message = message.get("error", "unknown control plane error")
                exc = StreamProtocolError(f"control plane error: {error_message}")
                self._fail(exc)
                raise exc

            logger.warning("stream[%s] unexpected control-plane message type %r", self._stream_id, msg_type)

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
