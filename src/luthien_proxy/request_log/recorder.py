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
from dataclasses import dataclass, field
from typing import Any

from luthien_proxy.request_log.sanitize import sanitize_headers
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Surface exceptions from fire-and-forget background tasks."""
    if not task.cancelled() and task.exception() is not None:
        logger.error("Background request log write failed", exc_info=task.exception())


@dataclass
class _PendingLog:
    """Accumulates data for a single log row before it's written to DB."""

    direction: str
    transaction_id: str
    session_id: str | None = None
    http_method: str | None = None
    url: str | None = None
    request_headers: dict[str, str] | None = None
    request_body: dict[str, Any] | None = None
    response_status: int | None = None
    response_headers: dict[str, str] | None = None
    response_body: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    duration_ms: float | None = None
    model: str | None = None
    is_streaming: bool = False
    endpoint: str | None = None


class RequestLogRecorder:
    """Captures HTTP-level request/response data and writes it to Postgres.

    Create one instance per proxy call (per transaction_id). Call methods
    at pipeline boundaries to accumulate data, then call ``flush()`` to
    write both rows to the database.

    When ``ENABLE_REQUEST_LOGGING`` is False, the ``create()`` classmethod
    returns a ``NoOpRequestLogRecorder`` instead.
    """

    def __init__(self, db_pool: DatabasePool, transaction_id: str) -> None:  # noqa: D107
        self._db_pool = db_pool
        self._transaction_id = transaction_id
        self._inbound = _PendingLog(direction="inbound", transaction_id=transaction_id)
        self._outbound = _PendingLog(direction="outbound", transaction_id=transaction_id)

    # -- Inbound (client ↔ proxy) ------------------------------------------

    def record_inbound_request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        session_id: str | None = None,
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
        self._inbound.model = model
        self._inbound.is_streaming = is_streaming
        self._inbound.endpoint = endpoint

    def record_inbound_response(
        self,
        *,
        status: int,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Capture the response sent back to the client."""
        self._inbound.response_status = status
        self._inbound.response_body = body
        if headers:
            self._inbound.response_headers = sanitize_headers(headers)
        self._inbound.completed_at = time.time()
        self._inbound.duration_ms = (self._inbound.completed_at - self._inbound.started_at) * 1000

    # -- Outbound (proxy ↔ backend) ----------------------------------------

    def record_outbound_request(
        self,
        *,
        body: dict[str, Any],
        model: str | None = None,
        is_streaming: bool = False,
        endpoint: str | None = None,
    ) -> None:
        """Capture the request sent to the backend LLM."""
        self._outbound.request_body = body
        self._outbound.session_id = self._inbound.session_id
        self._outbound.model = model
        self._outbound.is_streaming = is_streaming
        self._outbound.endpoint = endpoint
        self._outbound.started_at = time.time()

    def record_outbound_response(
        self,
        *,
        body: dict[str, Any] | None = None,
        status: int = 200,
    ) -> None:
        """Capture the response received from the backend LLM."""
        self._outbound.response_status = status
        self._outbound.response_body = body
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

    async def _write_logs(self) -> None:
        """Insert both inbound and outbound rows."""
        try:
            async with self._db_pool.connection() as conn:
                for pending in (self._inbound, self._outbound):
                    await conn.execute(
                        """
                        INSERT INTO request_logs (
                            transaction_id, session_id, direction,
                            http_method, url, request_headers, request_body,
                            response_status, response_headers, response_body,
                            started_at, completed_at, duration_ms,
                            model, is_streaming, endpoint
                        ) VALUES (
                            $1, $2, $3,
                            $4, $5, $6::jsonb, $7::jsonb,
                            $8, $9::jsonb, $10::jsonb,
                            to_timestamp($11), CASE WHEN $12::float IS NOT NULL THEN to_timestamp($12) END, $13,
                            $14, $15, $16
                        )
                        """,
                        pending.transaction_id,
                        pending.session_id,
                        pending.direction,
                        pending.http_method,
                        pending.url,
                        json.dumps(pending.request_headers) if pending.request_headers else None,
                        json.dumps(pending.request_body) if pending.request_body else None,
                        pending.response_status,
                        json.dumps(pending.response_headers) if pending.response_headers else None,
                        json.dumps(pending.response_body) if pending.response_body else None,
                        pending.started_at,
                        pending.completed_at,
                        pending.duration_ms,
                        pending.model,
                        pending.is_streaming,
                        pending.endpoint,
                    )
        except Exception:
            logger.exception("Failed to write request logs for %s", self._transaction_id)


class NoOpRequestLogRecorder(RequestLogRecorder):
    """Drop-in replacement that does nothing — used when logging is disabled.

    All methods are intentional no-ops.
    """

    def __init__(self) -> None:  # noqa: D107
        pass

    def record_inbound_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        pass

    def record_inbound_response(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        pass

    def record_outbound_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        pass

    def record_outbound_response(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        pass

    def flush(self) -> None:  # noqa: D102
        pass


def create_recorder(
    db_pool: DatabasePool | None,
    transaction_id: str,
    enabled: bool,
) -> RequestLogRecorder:
    """Factory that always returns a recorder — real or no-op based on config.

    Callers never need to null-check the return value.
    """
    if not enabled or db_pool is None:
        return NoOpRequestLogRecorder()
    return RequestLogRecorder(db_pool=db_pool, transaction_id=transaction_id)


__all__ = ["RequestLogRecorder", "NoOpRequestLogRecorder", "create_recorder"]
