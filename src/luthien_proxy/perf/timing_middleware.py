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
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Paths where Server-Timing is emitted.  /v1/messages is deliberately excluded.
_TIMED_PREFIXES: tuple[str, ...] = (
    "/api/history/",
    "/api/debug/",
    "/ui/fragments/",
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

    Args:
        name: Short identifier for the phase (e.g. ``"db"``, ``"serialize"``).

    Yields:
        Nothing — use as a plain context manager.

    Example::

        with time_phase("db"):
            rows = await conn.fetch(query)
    """
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


class ServerTimingMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that adds a ``Server-Timing`` header to filtered responses.

    Only paths starting with ``/api/history/``, ``/api/debug/``, or
    ``/ui/fragments/`` receive the header.  All other paths (including the hot
    ``/v1/messages`` path) pass through with zero overhead beyond a single
    ``str.startswith`` check.

    Timing phases are recorded by calling ``time_phase(name)`` anywhere in the
    request/response call stack.  Context isolation is guaranteed by
    ``contextvars.ContextVar``: each request gets its own fresh phase list.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: D102
        path = request.url.path
        should_time = path.startswith(_TIMED_PREFIXES)

        if not should_time:
            return await call_next(request)

        phases: list[tuple[str, float]] = []
        token = _phases_var.set(phases)
        try:
            response = await call_next(request)
        finally:
            _phases_var.reset(token)

        if phases:
            response.headers["Server-Timing"] = format_phases(phases)

        return response


__all__ = [
    "ServerTimingMiddleware",
    "time_phase",
    "format_phases",
]
