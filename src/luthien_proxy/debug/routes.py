"""Debug routes for gateway.

This module provides REST endpoints for debugging policy decisions:
- GET /api/debug/calls/{call_id} - Retrieve all events for a call
- GET /api/debug/calls/{call_id}/diff - Compute diff between original and final
- GET /api/debug/calls - List recent calls

Route handlers are thin wrappers that handle HTTP concerns (dependency injection,
error responses) and delegate business logic to the service layer.

All debug endpoints require admin authentication (same as /admin routes).

Serialization pattern: handlers return Response(content=model.model_dump_json(), ...)
with response_model= kept on the decorator for OpenAPI schema generation only.
FastAPI skips response validation when the handler returns a pre-built Response —
this is intentional to avoid double-serialization (Pydantic→dict→json→bytes twice).
The API contract snapshot tests (test_api_contract.py) provide regression coverage.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import Response

from luthien_proxy.auth import verify_admin_token
from luthien_proxy.dependencies import get_db_pool
from luthien_proxy.perf.timing_middleware import timed_json_response
from luthien_proxy.settings import client_error_detail
from luthien_proxy.utils.constants import DEBUG_CALLS_DEFAULT_LIMIT, DEBUG_CALLS_MAX_LIMIT

from .models import CallDiffResponse, CallEventsResponse, CallListResponse
from .service import fetch_call_diff, fetch_call_events, fetch_recent_calls

if TYPE_CHECKING:
    from luthien_proxy.utils import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/calls/{call_id}", response_model=CallEventsResponse)
async def get_call_events(
    call_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> Response:
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
        result = await fetch_call_events(call_id, db_pool)
        return timed_json_response(result)
    except ValueError as exc:
        # No events found
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to fetch events for call {call_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=client_error_detail(f"Database error: {exc}"))


@router.get("/calls/{call_id}/diff", response_model=CallDiffResponse)
async def get_call_diff(
    call_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> Response:
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
        result = await fetch_call_diff(call_id, db_pool)
        return timed_json_response(result)
    except ValueError as exc:
        # No events found
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to compute diff for call {call_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=client_error_detail(f"Database error: {exc}"))


@router.get("/calls", response_model=CallListResponse)
async def list_recent_calls(
    limit: int = Query(default=DEBUG_CALLS_DEFAULT_LIMIT, ge=1, le=DEBUG_CALLS_MAX_LIMIT),
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> Response:
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
        result = await fetch_recent_calls(limit, db_pool)
        return timed_json_response(result)
    except Exception as exc:
        logger.error(f"Failed to list recent calls: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=client_error_detail(f"Database error: {exc}"))


__all__ = ["router"]
