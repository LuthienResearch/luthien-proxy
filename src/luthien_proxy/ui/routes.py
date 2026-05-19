"""UI routes for gateway activity monitoring and debugging.

Protected routes require admin authentication (session cookie or API key).
Unauthenticated browser requests are redirected to /login.
"""

from __future__ import annotations

import logging
import os
from html import escape as html_escape
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from luthien_proxy.auth import check_auth_or_redirect, get_base_url, verify_admin_token
from luthien_proxy.dependencies import get_admin_key, get_db_pool, get_event_publisher
from luthien_proxy.history.service import fetch_session_turns_page, fetch_sessions_page
from luthien_proxy.observability.event_publisher import EventPublisherProtocol
from luthien_proxy.perf.timing_middleware import time_phase
from luthien_proxy.utils.cursor import decode_cursor
from luthien_proxy.utils.db import DatabasePool

router = APIRouter(prefix="", tags=["ui"])
logger = logging.getLogger(__name__)

# Static directory is relative to this module
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

# Template directory and Jinja2 environment
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
_jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)


@router.get("/api/activity/stream")
async def activity_stream(
    _: str = Depends(verify_admin_token),
    publisher: EventPublisherProtocol | None = Depends(get_event_publisher),
):
    """Server-Sent Events stream of activity events.

    This endpoint streams all gateway activity in real-time for debugging.
    Events include: request received, policy events, responses sent, etc.

    Returns:
        StreamingResponse with Server-Sent Events (text/event-stream)
    """
    if not publisher:
        raise HTTPException(
            status_code=503,
            detail="Activity stream unavailable (no event publisher configured)",
        )

    return FastAPIStreamingResponse(
        publisher.stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/")
async def landing_page():
    """Gateway landing page with links to all endpoints.

    Returns the HTML page with organized links to all debug, UI, and API endpoints.
    This page is public and shows available endpoints.
    """
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@router.get("/debug/activity")
async def debug_activity_monitor(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Raw SSE event stream viewer for debugging.

    Low-level view of all gateway events. For normal use, see /history
    and /conversation/live/{id} instead.
    """
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "activity_monitor.html"))


@router.get("/diffs")
async def diff_viewer(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Diff viewer UI.

    Returns the HTML page for viewing policy diffs with side-by-side comparison.
    Requires admin authentication.
    """
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "diff_viewer.html"))


@router.get("/policy-config")
async def policy_config(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Policy configuration UI.

    Returns the HTML page for configuring, enabling, and testing policies
    through a guided wizard interface.
    Requires admin authentication.
    """
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "policy_config.html"))


@router.get("/config")
async def config_dashboard(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Config dashboard — unified view of all gateway configuration with provenance."""
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "config_dashboard.html"))


@router.get("/credentials")
async def credentials_page(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Credentials and auth configuration management UI."""
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "credentials.html"))


@router.get("/inference-providers")
async def inference_providers_page(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Inference provider registry UI."""
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "inference_providers.html"))


@router.get("/request-logs/viewer")
async def request_logs_viewer(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Request/response logs viewer UI.

    Returns the HTML page for browsing and inspecting HTTP-level request logs.
    Requires admin authentication.
    """
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "request_logs.html"))


@router.get("/conversation/live/{conversation_id}")
async def conversation_live_view(
    request: Request,
    conversation_id: str,  # noqa: ARG001 - path param required by FastAPI
    admin_key: str | None = Depends(get_admin_key),
):
    """Live conversation viewer.

    Returns the HTML page for viewing a conversation in real-time with
    message timeline, tool calls, and policy divergence diffs.
    Requires admin authentication.
    """
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "conversation_live.html"))


@router.get("/client-setup")
async def client_setup(request: Request):
    """Client setup page with the proxy's actual URL.

    Injects the base URL derived from the incoming request so users can
    copy-paste directly into their shell. Public endpoint.
    """
    base_url = get_base_url(request)

    template_path = os.path.join(STATIC_DIR, "client_setup.html")
    with open(template_path) as f:
        html = f.read()

    html = html.replace("{{BASE_URL}}", html_escape(base_url))

    return HTMLResponse(html)


@router.get("/admin/{path:path}")
async def deprecated_admin_redirect(path: str):
    """Redirect old admin paths to new API location."""
    return RedirectResponse(url=f"/api/admin/{path}", status_code=301)


def _render_turns_fragment(turns: list[dict], next_cursor: str | None) -> str:
    """Render turns HTML fragment using Jinja2 template."""
    tpl = _jinja_env.get_template("fragments/turns.html")
    return tpl.render(turns=turns, next_cursor=next_cursor)


@router.get("/ui/fragments/sessions/{session_id}/turns")
async def fragment_session_turns(
    session_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    cursor: str | None = Query(default=None),
    admin_key: str | None = Depends(get_admin_key),
    db_pool: DatabasePool | None = Depends(get_db_pool),
):
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect

    if cursor is not None:
        try:
            decode_cursor(cursor, kind="turns")
        except ValueError as e:
            logger.debug("Rejected invalid turns cursor: %s", e)
            raise HTTPException(status_code=400, detail="Invalid cursor")

    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        result = await fetch_session_turns_page(session_id, cursor, limit, db_pool)
    except ValueError as e:
        logger.debug("Invalid cursor in fetch_session_turns_page: %s", e)
        raise HTTPException(status_code=400, detail="Invalid cursor")
    with time_phase("render"):
        html = _render_turns_fragment(result["turns"], result["next_cursor"])  # type: ignore[arg-type]
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


def _render_sessions_fragment(sessions: list[dict], next_cursor: str | None) -> str:
    """Render session list HTML fragment using Jinja2 template."""
    tpl = _jinja_env.get_template("fragments/sessions.html")
    return tpl.render(sessions=sessions, next_cursor=next_cursor)


@router.get("/ui/fragments/sessions")
async def fragment_sessions(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    q: str | None = Query(default=None),
    quick_filter: Literal["30days", "claude"] | None = Query(default=None, alias="filter"),
    admin_key: str | None = Depends(get_admin_key),
    db_pool: DatabasePool | None = Depends(get_db_pool),
):
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect

    if cursor is not None:
        try:
            decode_cursor(cursor, kind="sessions")
        except ValueError as e:
            logger.debug("Rejected invalid sessions cursor: %s", e)
            raise HTTPException(status_code=400, detail="Invalid cursor")

    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    result = await fetch_sessions_page(cursor, limit, db_pool, q=q, quick_filter=quick_filter)
    with time_phase("render"):
        html = _render_sessions_fragment(result["sessions"], result["next_cursor"])  # type: ignore[arg-type]
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


__all__ = ["router"]
