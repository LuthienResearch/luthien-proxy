"""Record HTTP-level request/response data for debugging.

The RequestLogRecorder captures inbound and outbound HTTP details at
pipeline boundaries. Each proxy call produces two log rows:

  - **inbound**: client → proxy request, plus proxy → client response
  - **outbound**: proxy → backend request, plus backend → proxy response

All writes are fire-and-forget background tasks so they never block
the request path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from luthien_proxy.request_log.models import _PendingLog, insert_log_row
from luthien_proxy.request_log.sanitize import sanitize_headers
from luthien_proxy.utils.db import DatabasePool, DatabaseWriteError

logger = logging.getLogger(__name__)

# Agentic multi-provider captures can exceed 1 MB; keep full bodies for transcript replay.
MAX_BODY_BYTES = 8_388_608  # 8 MB


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Surface exceptions from fire-and-forget background tasks."""
    if not task.cancelled() and task.exception() is not None:
        logger.error("Background request log write failed", exc_info=task.exception())


class RequestLogRecorder:
    """Captures HTTP-level request/response data and writes it to the database.

    Create one instance per proxy call (per transaction_id). Call methods
    at pipeline boundaries to accumulate data, then call ``flush()`` to
    write both rows to the database.

    When ``ENABLE_REQUEST_LOGGING`` is False, the ``create()`` classmethod
    returns a ``NoOpRequestLogRecorder`` instead.
    """

    dropped_writes: int = 0

    def __init__(  # noqa: D107
        self,
        db_pool: DatabasePool,
        transaction_id: str,
        *,
        on_commit: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._db_pool = db_pool
        self._transaction_id = transaction_id
        self._on_commit = on_commit
        self._inbound = _PendingLog(direction="inbound", transaction_id=transaction_id)
        self._outbound = _PendingLog(direction="outbound", transaction_id=transaction_id)

    # -- Inbound (client ↔ proxy) ------------------------------------------

    def record_inbound_request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        session_id: str | None = None,
        user_id: str | None = None,
        model: str | None = None,
        is_streaming: bool = False,
        endpoint: str | None = None,
    ) -> None:
        """Capture the incoming client request."""
        self._inbound.http_method = method
        self._inbound.url = url
        self._inbound.request_headers = sanitize_headers(headers)
        self._inbound.request_body = body
        self._inbound.session_id = session_id
        self._inbound.user_id = user_id
        self._inbound.model = model
        self._inbound.is_streaming = is_streaming
        self._inbound.endpoint = endpoint

    def record_inbound_response(
        self,
        *,
        status: int,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        error: str | None = None,
    ) -> None:
        """Capture the response sent back to the client."""
        self._inbound.response_status = status
        self._inbound.response_body = body
        self._inbound.error = error
        if headers:
            self._inbound.response_headers = sanitize_headers(headers)
        self._inbound.completed_at = time.time()
        self._inbound.duration_ms = (self._inbound.completed_at - self._inbound.started_at) * 1000

    # -- Outbound (proxy ↔ backend) ----------------------------------------

    def record_outbound_request(
        self,
        *,
        body: dict[str, Any] | None,
        method: str = "POST",
        url: str | None = None,
        model: str | None = None,
        is_streaming: bool = False,
        endpoint: str | None = None,
    ) -> None:
        """Capture the request sent to the backend LLM."""
        self._outbound.http_method = method
        self._outbound.url = url
        self._outbound.request_body = body
        self._outbound.session_id = self._inbound.session_id
        self._outbound.user_id = self._inbound.user_id
        self._outbound.model = model
        self._outbound.is_streaming = is_streaming
        self._outbound.endpoint = endpoint
        self._outbound.started_at = time.time()

    def record_outbound_response(
        self,
        *,
        body: dict[str, Any] | None = None,
        status: int = 200,
        error: str | None = None,
    ) -> None:
        """Capture the response received from the backend LLM."""
        self._outbound.response_status = status
        self._outbound.response_body = body
        self._outbound.error = error
        self._outbound.completed_at = time.time()
        self._outbound.duration_ms = (self._outbound.completed_at - self._outbound.started_at) * 1000

    # -- Flush to DB -------------------------------------------------------

    def flush(self) -> None:
        """Write both log rows to the database as a background task.

        Safe to call at the end of the pipeline — won't block the response.
        """
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._write_logs())
            task.add_done_callback(_log_task_exception)
        except RuntimeError:
            logger.debug("No running event loop; skipping request log flush")

    @staticmethod
    def _serialize_body(body: dict[str, Any] | None) -> str | None:
        """JSON-serialize a body dict, truncating if it exceeds MAX_BODY_BYTES."""
        if body is None:
            return None
        serialized = json.dumps(body)
        if len(serialized) > MAX_BODY_BYTES:
            return json.dumps({"_truncated": True, "_original_size_bytes": len(serialized)})
        return serialized

    async def _write_logs(self) -> None:
        """Insert both inbound and outbound rows."""
        try:
            async with self._db_pool.connection() as conn:
                async with conn.transaction():
                    cache: dict[int, str | None] = {}

                    def serialize_body(body: dict[str, Any] | None) -> str | None:
                        key = id(body)
                        if key not in cache:
                            cache[key] = self._serialize_body(body)
                        return cache[key]

                    for pending in (self._inbound, self._outbound):
                        await insert_log_row(conn, pending, serialize_body)
        except DatabaseWriteError as exc:
            RequestLogRecorder.dropped_writes += 1
            logger.warning(
                "Failed to write request logs for %s (%d total dropped): %s",
                self._transaction_id,
                RequestLogRecorder.dropped_writes,
                exc.cause,
            )
            return

        if self._on_commit is None:
            return

        try:
            await self._on_commit(self._transaction_id)
        except Exception:
            logger.warning(
                "Request log post-commit callback failed for %s",
                self._transaction_id,
                exc_info=True,
            )


class NoOpRequestLogRecorder(RequestLogRecorder):
    """Drop-in replacement that does nothing — used when logging is disabled.

    All methods are intentional no-ops.
    """

    def __init__(  # noqa: D107, ARG002
        self, *, on_commit: Callable[[str], Awaitable[None]] | None = None
    ) -> None:
        pass

    def record_inbound_request(  # noqa: D102, ARG002
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        session_id: str | None = None,
        user_id: str | None = None,
        model: str | None = None,
        is_streaming: bool = False,
        endpoint: str | None = None,
    ) -> None:
        pass

    def record_inbound_response(  # noqa: D102, ARG002
        self,
        *,
        status: int,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        error: str | None = None,
    ) -> None:
        pass

    def record_outbound_request(  # noqa: D102, ARG002
        self,
        *,
        body: dict[str, Any],
        method: str = "POST",
        url: str | None = None,
        model: str | None = None,
        is_streaming: bool = False,
        endpoint: str | None = None,
    ) -> None:
        pass

    def record_outbound_response(  # noqa: D102, ARG002
        self,
        *,
        body: dict[str, Any] | None = None,
        status: int = 200,
        error: str | None = None,
    ) -> None:
        pass

    def flush(self) -> None:  # noqa: D102
        pass


def create_recorder(
    db_pool: DatabasePool | None,
    transaction_id: str,
    enabled: bool,
    *,
    on_commit: Callable[[str], Awaitable[None]] | None = None,
) -> RequestLogRecorder:
    """Factory that always returns a recorder — real or no-op based on config.

    Callers never need to null-check the return value.
    """
    if not enabled or db_pool is None:
        return NoOpRequestLogRecorder(on_commit=on_commit)
    return RequestLogRecorder(db_pool=db_pool, transaction_id=transaction_id, on_commit=on_commit)


__all__ = ["RequestLogRecorder", "NoOpRequestLogRecorder", "create_recorder"]
