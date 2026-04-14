"""Routes for conversation history viewer.

Provides endpoints for:
- Listing recent sessions
- Viewing session details
- Exporting sessions to markdown
- User label management (assign display names to user hashes)
- HTML UI pages
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from luthien_proxy.auth import check_auth_or_redirect, verify_admin_token
from luthien_proxy.dependencies import get_admin_key, get_db_pool
from luthien_proxy.utils.constants import (
    HISTORY_SESSIONS_DEFAULT_LIMIT,
    HISTORY_SESSIONS_MAX_LIMIT,
)
from luthien_proxy.utils.db import DatabasePool

from .models import SessionDetail, SessionListResponse
from .service import export_session_jsonl, export_session_markdown, fetch_session_detail, fetch_session_list

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])
api_router = APIRouter(prefix="/api/history", tags=["history-api"])


class UserLabelRequest(BaseModel):
    """Request body for setting a user display name."""

    display_name: str = Field(
        ...,
        max_length=255,
        description="Human-readable display name for the user",
    )


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


@router.get("/session/{session_id}")
async def deprecated_history_detail_redirect(session_id: str):
    """Redirect old history detail path to live conversation view.

    No auth check here — the redirect target handles auth.
    """
    return RedirectResponse(url=f"/conversation/live/{quote(session_id, safe='')}", status_code=301)


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
    user_hash: str | None = Query(
        default=None,
        description="Filter sessions by user hash",
    ),
) -> SessionListResponse:
    """List recent sessions with summaries.

    Returns a list of session summaries ordered by most recent activity,
    including turn counts, policy interventions, and models used.
    Supports pagination via limit and offset parameters.
    Optionally filters by user_hash.
    """
    return await fetch_session_list(limit, db_pool, offset, user_hash=user_hash)


@api_router.get("/users")
async def list_users(
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
    limit: int = Query(default=500, ge=1, le=5000, description="Max users to return"),
    offset: int = Query(default=0, ge=0, description="Users to skip for pagination"),
) -> dict:
    """List distinct user hashes with any assigned labels.

    Queries session_summaries (one row per session, indexed on user_hash)
    rather than conversation_calls (one row per call) for a much smaller
    DISTINCT scan on deployments with many calls per session.
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT user_hash FROM session_summaries
            WHERE user_hash IS NOT NULL
            ORDER BY user_hash
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
        user_hashes = [str(row["user_hash"]) for row in rows]
        if user_hashes:
            placeholders = ",".join(f"${i + 1}" for i in range(len(user_hashes)))
            label_rows = await conn.fetch(
                f"SELECT user_hash, display_name FROM user_labels WHERE user_hash IN ({placeholders})",
                *user_hashes,
            )
        else:
            label_rows = []
    labels = {str(row["user_hash"]): str(row["display_name"]) for row in label_rows}
    return {
        "users": user_hashes,
        "labels": labels,
    }


@api_router.get("/user-labels")
async def list_user_labels(
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """Return all user labels.

    Returns a mapping from user_hash to display_name for all labeled users.
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch("SELECT user_hash, display_name FROM user_labels ORDER BY display_name")
    return {"labels": {str(row["user_hash"]): str(row["display_name"]) for row in rows}}


@api_router.put("/user-labels/{user_hash}")
async def set_user_label(
    user_hash: str,
    body: UserLabelRequest,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """Create or update a display name for a user hash."""
    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="display_name must not be blank")
    now = datetime.now(timezone.utc)
    async with db_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO user_labels (user_hash, display_name, created_at, updated_at)
               VALUES ($1, $2, $3, $3)
               ON CONFLICT (user_hash) DO UPDATE SET
                   display_name = EXCLUDED.display_name,
                   updated_at = EXCLUDED.updated_at""",
            user_hash,
            display_name,
            now,
        )
    return {"user_hash": user_hash, "display_name": display_name}


@api_router.delete("/user-labels/{user_hash}")
async def delete_user_label(
    user_hash: str,
    _: str = Depends(verify_admin_token),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """Remove a user label."""
    async with db_pool.connection() as conn:
        await conn.execute("DELETE FROM user_labels WHERE user_hash = $1", user_hash)
    return {"deleted": True}


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
        logger.warning(f"Session not found for JSONL export: {repr(e)}")
        raise HTTPException(status_code=404, detail="Session not found.") from None

    jsonl = export_session_jsonl(session)

    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    return PlainTextResponse(
        content=jsonl,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="conversation_{safe_id}.jsonl"'},
    )


__all__ = ["router", "api_router"]
