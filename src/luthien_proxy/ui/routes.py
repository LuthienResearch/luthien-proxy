"""UI routes for gateway activity monitoring and debugging.

Protected routes require admin authentication (session cookie or API key).
Unauthenticated browser requests are redirected to /login.
"""

from __future__ import annotations

import os
from html import escape as html_escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from redis.asyncio import Redis

from luthien_proxy.auth import check_auth_or_redirect, get_base_url, verify_admin_token
from luthien_proxy.dependencies import get_admin_key, get_redis_client
from luthien_proxy.observability import stream_activity_events

router = APIRouter(prefix="", tags=["ui"])

# Static directory is relative to this module
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@router.get("/activity/stream")
async def activity_stream(
    _: str = Depends(verify_admin_token),
    redis_client: Redis | None = Depends(get_redis_client),
):
    """Server-Sent Events stream of activity events.

    This endpoint streams all gateway activity in real-time for debugging.
    Events include: request received, policy events, responses sent, etc.

    Returns:
        StreamingResponse with Server-Sent Events (text/event-stream)
    """
    if not redis_client:
        raise HTTPException(
            status_code=503,
            detail="Activity stream unavailable (Redis not connected)",
        )

    return FastAPIStreamingResponse(
        stream_activity_events(redis_client),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get("/")
async def landing_page():
    """Gateway landing page with links to all endpoints.

    Returns the HTML page with organized links to all debug, UI, and API endpoints.
    This page is public and shows available endpoints.
    """
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@router.get("/activity/monitor")
async def activity_monitor(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Activity monitor UI.

    Returns the HTML page for viewing the activity stream in real-time.
    Requires admin authentication.
    """
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "activity_monitor.html"))


@router.get("/debug/diff")
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


__all__ = ["router"]
