"""Routes for conversation history viewer.

Provides endpoints for:
- Listing recent sessions
- Viewing session details
- Exporting sessions to markdown
- HTML UI pages
"""

from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from luthien_proxy.auth import verify_admin_token
from luthien_proxy.dependencies import get_admin_key, get_db_pool
from luthien_proxy.session import get_session_user
from luthien_proxy.utils.constants import (
    HISTORY_SESSIONS_DEFAULT_LIMIT,
    HISTORY_SESSIONS_MAX_LIMIT,
)
from luthien_proxy.utils.db import DatabasePool

from .models import SessionDetail, SessionListResponse
from .service import export_session_markdown, fetch_session_detail, fetch_session_list

router = APIRouter(prefix="/history", tags=["history"])

# Static directory for HTML templates
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


def _check_auth_or_redirect(request: Request, admin_key: str | None) -> RedirectResponse | None:
    """Check if user is authenticated, return redirect if not.

    Returns None if authenticated, RedirectResponse to login otherwise.
    """
    if not admin_key:
        return None  # No auth configured, allow access

    session = get_session_user(request, admin_key)
    if session:
        return None  # Authenticated via session

    # Not authenticated - redirect to login
    next_url = quote(str(request.url.path), safe="")
    return RedirectResponse(url=f"/login?error=required&next={next_url}", status_code=303)


# === HTML UI Routes ===


@router.get("")
async def history_list_page(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Conversation history list UI.

    Returns the HTML page for browsing recent sessions.
    Requires admin authentication.
    """
    redirect = _check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "history_list.html"))


@router.get("/session/{session_id}")
async def history_detail_page(
    request: Request,
    session_id: str,
    admin_key: str | None = Depends(get_admin_key),
    db_pool: DatabasePool = Depends(get_db_pool),
):
    """Conversation history detail UI.

    Returns the HTML page for viewing a specific session's conversation.
    Requires admin authentication. Returns 404 if session doesn't exist.
    """
    redirect = _check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect

    # Validate session exists before serving HTML
    try:
        await fetch_session_detail(session_id, db_pool)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    return FileResponse(os.path.join(STATIC_DIR, "history_detail.html"))


# === API Routes ===


@router.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
    limit: int = Query(
        default=HISTORY_SESSIONS_DEFAULT_LIMIT,
        ge=1,
        le=HISTORY_SESSIONS_MAX_LIMIT,
        description="Maximum number of sessions to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of sessions to skip for pagination",
    ),
) -> SessionListResponse:
    """List recent sessions with summaries.

    Returns a list of session summaries ordered by most recent activity,
    including turn counts, policy interventions, and models used.
    Supports pagination via limit and offset parameters.
    """
    return await fetch_session_list(limit, db_pool, offset)


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> SessionDetail:
    """Get full session detail with conversation turns.

    Returns the complete conversation history for a session,
    including all messages, tool calls, and policy annotations.
    """
    try:
        return await fetch_session_detail(session_id, db_pool)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/api/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> PlainTextResponse:
    """Export session as markdown.

    Returns the conversation history formatted as a markdown document,
    suitable for saving or sharing.
    """
    try:
        session = await fetch_session_detail(session_id, db_pool)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    markdown = export_session_markdown(session)

    # Sanitize session_id for filename
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="conversation_{safe_id}.md"'},
    )


__all__ = ["router"]
