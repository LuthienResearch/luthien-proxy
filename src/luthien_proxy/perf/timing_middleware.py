"""Server-Timing middleware for admin/debug/UI paths.

Records per-request timing phases via contextvars and appends a ``Server-Timing``
header on responses whose path starts with ``/api/history/``, ``/api/debug/``, or
``/ui/fragments/``.  All other paths (including ``/v1/messages``) are untouched.

Usage::

    from luthien_proxy.perf.timing_middleware import time_phase, ServerTimingMiddleware

    # Inside a request handler or service function:
    with time_phase("db"):
        rows = await db.fetch(query)

    with time_phase("serialize"):
        payload = serialize(rows)

    # In FastAPI app setup (handled by P14):
    app.add_middleware(ServerTimingMiddleware)
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from pydantic import BaseModel
from starlette.responses import Response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Paths where Server-Timing is emitted.  /v1/messages is deliberately excluded.
_TIMED_PREFIXES: tuple[str, ...] = (
    "/api/history/",
    "/api/debug/",
    "/ui/fragments/",
    # /api/activity/stream and other SSE endpoints are intentionally excluded:
    # long-lived connections collect phases for the full stream duration,
    # making the Server-Timing header meaningless as a request-level metric.
)

# Per-request phase list: list of (name, elapsed_ms) tuples.
# A new list is injected at the start of each request by ServerTimingMiddleware
# so phases never bleed across requests, even under concurrent load.
_phases_var: ContextVar[list[tuple[str, float]]] = ContextVar("_luthien_timing_phases")


@contextmanager
def time_phase(name: str) -> Generator[None, None, None]:
    """Record the wall-clock duration of a code block as a timing phase.

    The elapsed milliseconds are appended to the current request's phase list
    (stored in a ``ContextVar``).  If called outside a ``ServerTimingMiddleware``
    request context the phase is silently discarded.  Phases are recorded even
    when the block raises — the ``finally`` clause always appends the elapsed time.

    Public API: intentionally exported without a leading underscore so that
    perf/db.py and test infrastructure can call it directly without reaching
    into a private symbol.

    Args:
        name: Short identifier for the phase (e.g. ``"db"``, ``"serialize"``).

    Yields:
        Nothing — use as a plain context manager.

    Example::

        with time_phase("db"):
            rows = await conn.fetch(query)
    """
    if "\r" in name or "\n" in name:
        raise ValueError(f"time_phase name must not contain \\r or \\n: {name!r}")
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        phases = _phases_var.get(None)
        if phases is not None:
            phases.append((name, elapsed_ms))


def format_phases(phases: list[tuple[str, float]]) -> str:
    """Format a list of timing phases as a ``Server-Timing`` header value.

    Args:
        phases: Ordered list of ``(name, elapsed_ms)`` tuples.

    Returns:
        Header value string, e.g. ``"db;dur=12.3, serialize;dur=4.5"``.
        Returns an empty string if ``phases`` is empty.

    Example::

        >>> format_phases([("db", 12.3), ("serialize", 4.5)])
        'db;dur=12.3, serialize;dur=4.5'
    """
    return ", ".join(f"{name};dur={elapsed_ms:.1f}" for name, elapsed_ms in phases)


class ServerTimingMiddleware:
    """Pure-ASGI middleware that adds a ``Server-Timing`` header to filtered responses.

    Implemented as a plain ASGI callable (not ``BaseHTTPMiddleware``) to avoid
    Starlette's pipe-buffering wrapper around ``call_next``, which materialises
    streaming responses in memory and breaks ContextVar propagation in some
    Starlette versions.  This implementation wraps only ``send`` — the inner app
    runs unmodified and streaming chunks pass through untouched.

    Only paths starting with ``/api/history/``, ``/api/debug/``, or
    ``/ui/fragments/`` receive the header.  All other paths (including the hot
    ``/v1/messages`` path) pass through with zero overhead beyond a single
    ``str.startswith`` check on the ASGI scope.

    Timing phases are recorded by calling ``time_phase(name)`` anywhere in the
    request/response call stack.  Context isolation is guaranteed by
    ``contextvars.ContextVar``: each request gets its own fresh phase list.

    ContextVar constraint: if any ``BaseHTTPMiddleware`` sits between this
    middleware and the route handler (e.g. ``StaticCacheMiddleware`` in
    ``main.py``), ContextVar propagation may silently break for streaming
    responses on that path.  Do not call ``time_phase`` from inside a
    ``StreamingResponse`` body generator — the phase will be lost.

    Phases after ``http.response.start``: the ``Server-Timing`` header is
    finalized at response-start time.  Any ``time_phase`` block that runs
    during body streaming (e.g. in a generator) will not appear in the header.
    """

    def __init__(self, app: ASGIApp) -> None:  # noqa: D107
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: D102
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not path.startswith(_TIMED_PREFIXES):
            await self.app(scope, receive, send)
            return

        phases: list[tuple[str, float]] = []
        token = _phases_var.set(phases)
        try:
            await self.app(scope, receive, _make_send_with_timing(send, phases))
        finally:
            _phases_var.reset(token)


def timed_json_response(model: BaseModel) -> Response:
    """Serialize a Pydantic model to a JSON Response, recording serialize time.

    Wraps ``model.model_dump_json()`` in a ``time_phase("serialize")`` block and
    returns a ``starlette.responses.Response`` with ``media_type="application/json"``.
    Use in route handlers instead of returning the model directly to avoid the
    double-serialization that occurs when FastAPI validates a ``response_model``
    return value (Pydantic→dict→json twice).

    Args:
        model: A ``pydantic.BaseModel`` instance.

    Returns:
        A pre-serialized JSON ``Response``.
    """
    with time_phase("serialize"):
        body: str = model.model_dump_json()
    return Response(content=body, media_type="application/json")


def _make_send_with_timing(
    send: Send,
    phases: list[tuple[str, float]],
) -> Callable[[Message], Awaitable[None]]:
    """Return a wrapped ``send`` that injects Server-Timing on http.response.start."""

    async def send_with_timing(message: Message) -> None:
        if message["type"] == "http.response.start" and phases:
            headers = [h for h in message.get("headers", []) if h[0].lower() != b"server-timing"]
            headers.append((b"server-timing", format_phases(phases).encode()))
            message = {**message, "headers": headers}
        await send(message)

    return send_with_timing


__all__ = [
    "ServerTimingMiddleware",
    "time_phase",
    "format_phases",
    "timed_json_response",
]
