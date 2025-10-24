# ABOUTME: Debug REST endpoints for querying conversation events and computing diffs
# ABOUTME: Thin FastAPI route handlers that delegate to service layer

"""Debug routes for V2 gateway.

This module provides REST endpoints for debugging policy decisions:
- GET /v2/debug/calls/{call_id} - Retrieve all events for a call
- GET /v2/debug/calls/{call_id}/diff - Compute diff between original and final
- GET /v2/debug/calls - List recent calls

Route handlers are thin wrappers that handle HTTP concerns (dependency injection,
error responses) and delegate business logic to the service layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .models import CallDiffResponse, CallEventsResponse, CallListResponse
from .service import fetch_call_diff, fetch_call_events, fetch_recent_calls

if TYPE_CHECKING:
    from luthien_proxy.utils import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/debug", tags=["debug"])


def get_db_pool(request: Request) -> db.DatabasePool | None:
    """Dependency to get database pool from app state.

    The db_pool is set during app startup in main.py's lifespan handler.

    For testing, you can either:
    1. Pass db_pool directly to endpoint functions (bypassing dependency injection)
    2. Override this dependency using app.dependency_overrides[get_db_pool]
    3. Set app.state.db_pool in test setup

    Args:
        request: FastAPI request object containing app state

    Returns:
        Database pool if configured, None otherwise
    """
    return getattr(request.app.state, "db_pool", None)


# === Endpoints ===


@router.get("/calls/{call_id}", response_model=CallEventsResponse)
async def get_call_events(
    call_id: str,
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> CallEventsResponse:
    """Retrieve all conversation events for a specific call_id.

    Args:
        call_id: Unique identifier for the request/response cycle
        db_pool: Database connection pool (injected by FastAPI)

    Returns:
        All events for the call, plus link to Tempo trace

    Raises:
        HTTPException: If database is not configured or query fails
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        return await fetch_call_events(call_id, db_pool)
    except ValueError as exc:
        # No events found
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to fetch events for call {call_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


@router.get("/calls/{call_id}/diff", response_model=CallDiffResponse)
async def get_call_diff(
    call_id: str,
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> CallDiffResponse:
    """Compute diff between original and final request/response for a call.

    Args:
        call_id: Unique identifier for the request/response cycle
        db_pool: Database connection pool (injected by FastAPI)

    Returns:
        Structured diff showing what the policy changed

    Raises:
        HTTPException: If database is not configured or query fails
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        return await fetch_call_diff(call_id, db_pool)
    except ValueError as exc:
        # No events found
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to compute diff for call {call_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


@router.get("/calls", response_model=CallListResponse)
async def list_recent_calls(
    limit: int = Query(default=50, ge=1, le=1000),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> CallListResponse:
    """List recent calls with event counts.

    Args:
        limit: Maximum number of calls to return (1-1000)
        db_pool: Database connection pool (injected by FastAPI)

    Returns:
        List of recent calls ordered by latest timestamp

    Raises:
        HTTPException: If database is not configured or query fails
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        return await fetch_recent_calls(limit, db_pool)
    except Exception as exc:
        logger.error(f"Failed to list recent calls: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


__all__ = ["router"]
