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
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from luthien_proxy.auth import check_auth_or_redirect, verify_admin_token
from luthien_proxy.dependencies import get_admin_key, get_db_pool
from luthien_proxy.utils.constants import (
    HISTORY_SESSIONS_DEFAULT_LIMIT,
    HISTORY_SESSIONS_MAX_LIMIT,
)
from luthien_proxy.utils.db import DatabasePool

from . import user_labels as user_labels_service
from .models import SessionDetail, SessionListResponse, SessionSearchParams
from .service import fetch_session_list, stream_session_detail_json, stream_session_jsonl, stream_session_markdown


class UserLabelRequest(BaseModel):
    """Request body for assigning a display name to a user_id."""

    display_name: str = Field(
        ...,
        max_length=user_labels_service.MAX_DISPLAY_NAME_LENGTH,
        description="Human-readable display name for the user",
    )


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
        description="Lower bound (inclusive) on session last activity, ISO 8601. Interpreted as UTC.",
    ),
    to_time: datetime | None = Query(
        default=None,
        alias="to",
        description=(
            "Upper bound (inclusive) on session last activity, ISO 8601. Interpreted as UTC. "
            "Inclusive at the timestamp level: to include all of a calendar day, pass end-of-day "
            "(e.g. 2026-04-30T23:59:59)."
        ),
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


# --- User labels ---


@api_router.get("/users")
async def list_users(
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
    limit: int = Query(default=500, ge=1, le=5000, description="Max users to return"),
    offset: int = Query(default=0, ge=0, description="Users to skip for pagination"),
) -> dict[str, object]:
    """List distinct user_ids seen across sessions, with any assigned labels.

    Returns ``{"users": [...], "labels": {user_id: display_name}}``. Backs the
    history UI's per-user filter dropdown so it can list every known user, not
    just the ones on the currently loaded page of sessions.
    """
    return await user_labels_service.list_users(db_pool, limit=limit, offset=offset)


@api_router.get("/user-labels")
async def list_user_labels(
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict[str, dict[str, str]]:
    """Return all user labels as ``{"labels": {user_id: display_name}}``."""
    return {"labels": await user_labels_service.list_labels(db_pool)}


@api_router.put("/user-labels/{user_id}")
async def set_user_label(
    user_id: str,
    body: UserLabelRequest,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict[str, str]:
    """Create or update the display name for a user_id."""
    try:
        display_name = await user_labels_service.set_label(db_pool, user_id, body.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"user_id": user_id, "display_name": display_name}


@api_router.delete("/user-labels/{user_id}")
async def delete_user_label(
    user_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict[str, bool]:
    """Remove a user label (no-op if none exists)."""
    await user_labels_service.delete_label(db_pool, user_id)
    return {"deleted": True}


@api_router.get("/sessions/{session_id}", responses={200: {"model": SessionDetail}})
async def get_session(
    session_id: str,
    offset: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> StreamingResponse:
    """Get a window of session turns in chronological display order.

    ``offset`` is a zero-based chronological turn offset. When omitted, the
    newest page is returned. ``limit`` defaults to 50 and is capped at 200.
    """
    try:
        stream = stream_session_detail_json(session_id, db_pool, offset=offset, limit=limit)
        first_chunk = await anext(stream)
    except ValueError as e:
        logger.warning(f"Session not found: {repr(e)}")
        raise HTTPException(status_code=404, detail="Session not found.") from None

    async def body():
        yield first_chunk
        async for chunk in stream:
            yield chunk

    return StreamingResponse(body(), media_type="application/json")


@api_router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> StreamingResponse:
    """Export session as markdown.

    Returns the conversation history formatted as a markdown document,
    suitable for saving or sharing.
    """
    try:
        stream = stream_session_markdown(session_id, db_pool)
        first_chunk = await anext(stream)
    except ValueError as e:
        logger.warning(f"Session not found for export: {repr(e)}")
        raise HTTPException(status_code=404, detail="Session not found.") from None

    # Sanitize session_id for filename
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    async def body():
        yield first_chunk
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        body(),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="conversation_{safe_id}.md"'},
    )


@api_router.get("/sessions/{session_id}/export/jsonl")
async def export_session_jsonl_endpoint(
    session_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> StreamingResponse:
    """Export session as JSONL (one JSON line per turn).

    Returns the conversation history as JSONL, suitable for
    programmatic analysis and log ingestion.
    """
    try:
        stream = stream_session_jsonl(session_id, db_pool)
        first_chunk = await anext(stream)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None

    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    async def body():
        yield first_chunk
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="conversation_{safe_id}.jsonl"'},
    )


__all__ = ["router", "api_router"]
