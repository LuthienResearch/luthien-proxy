"""Routes for conversation history viewer.

Provides endpoints for:
- Listing recent sessions
- Viewing session details
- Exporting sessions to markdown
- HTML UI pages
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse

from luthien_proxy.auth import check_auth_or_redirect, verify_admin_token
from luthien_proxy.dependencies import get_admin_key, get_db_pool
from luthien_proxy.utils.constants import (
    HISTORY_SESSIONS_DEFAULT_LIMIT,
    HISTORY_SESSIONS_MAX_LIMIT,
)
from luthien_proxy.utils.db import DatabasePool

from .models import SessionDetail, SessionListResponse, SessionSearchParams
from .service import export_session_jsonl, export_session_markdown, fetch_session_detail, fetch_session_list

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])
api_router = APIRouter(prefix="/api/history", tags=["history-api"])

# Static directory for HTML templates
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


# --- UI Pages ---


@router.get("")
async def history_list_page(
    request: Request,
    admin_key: str | None = Depends(get_admin_key),
):
    """Conversation history list UI.

    Returns the HTML page for browsing recent sessions.
    Requires admin authentication.
    """
    redirect = check_auth_or_redirect(request, admin_key)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC_DIR, "history_list.html"))


# --- JSON API Endpoints ---


@api_router.get("/sessions", response_model=SessionListResponse)
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
    user_id: str | None = Query(
        default=None,
        description=(
            "Filter by exact user_id. Matches the user_id extracted from "
            "X-Luthien-User-Id header (when TRUST_USER_ID_HEADER=true) or JWT sub claim."
        ),
    ),
    model: str | None = Query(
        default=None,
        description="Filter to sessions that used this exact model (matches final_model on any turn).",
    ),
    from_time: datetime | None = Query(
        default=None,
        alias="from",
        description="Lower bound (inclusive) on session last activity, ISO 8601.",
    ),
    to_time: datetime | None = Query(
        default=None,
        alias="to",
        description="Upper bound (inclusive) on session last activity, ISO 8601.",
    ),
    q: str | None = Query(
        default=None,
        description=(
            "Full-text content search over conversation text (porter-stemmed, "
            "terms ANDed). A session matches if any turn matches. Note: the index "
            "currently includes gateway-injected policy-context text, so queries "
            "for policy-context terms may over-match on policy-active sessions."
        ),
    ),
    policy_intervention: bool = Query(
        default=False,
        description="When true, return only sessions that had at least one policy intervention.",
    ),
) -> SessionListResponse:
    """List recent sessions with summaries.

    Returns a list of session summaries ordered by most recent activity,
    including turn counts, policy interventions, and models used.
    Supports pagination via limit and offset, plus optional server-side
    filters (user_id, model, from/to time range, full-text q, policy_intervention).
    ``total`` reflects the count after filters are applied.
    """
    search = SessionSearchParams(
        model=model,
        from_time=from_time,
        to_time=to_time,
        q=q,
        policy_intervention=policy_intervention,
    )
    return await fetch_session_list(limit, db_pool, offset, user_id=user_id, search=search)


@api_router.get("/sessions/{session_id}", response_model=SessionDetail)
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
        logger.warning(f"Session not found: {repr(e)}")
        raise HTTPException(status_code=404, detail="Session not found.") from None


@api_router.get("/sessions/{session_id}/export")
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
        logger.warning(f"Session not found for export: {repr(e)}")
        raise HTTPException(status_code=404, detail="Session not found.") from None

    markdown = export_session_markdown(session)

    # Sanitize session_id for filename
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="conversation_{safe_id}.md"'},
    )


@api_router.get("/sessions/{session_id}/export/jsonl")
async def export_session_jsonl_endpoint(
    session_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> PlainTextResponse:
    """Export session as JSONL (one JSON line per turn).

    Returns the conversation history as JSONL, suitable for
    programmatic analysis and log ingestion.
    """
    try:
        session = await fetch_session_detail(session_id, db_pool)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None

    jsonl = export_session_jsonl(session)

    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    return PlainTextResponse(
        content=jsonl,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="conversation_{safe_id}.jsonl"'},
    )


__all__ = ["router", "api_router"]
