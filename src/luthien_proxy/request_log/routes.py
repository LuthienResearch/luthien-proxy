"""API routes for querying request/response logs.

All endpoints require admin authentication.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

from luthien_proxy.auth import verify_admin_token
from luthien_proxy.dependencies import get_db_pool

from .models import RequestLogDetailResponse, RequestLogListResponse
from .service import get_transaction_logs, list_request_logs

if TYPE_CHECKING:
    from luthien_proxy.utils import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/request-logs", tags=["request-logs"])


@router.get("", response_model=RequestLogListResponse)
async def list_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    direction: str | None = Query(default=None, pattern="^(inbound|outbound)$"),
    endpoint: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    status: int | None = Query(default=None),
    model: str | None = Query(default=None),
    after: str | None = Query(default=None),
    before: str | None = Query(default=None),
    search: str | None = Query(default=None),
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> RequestLogListResponse:
    """List request/response logs with optional filters."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        return await list_request_logs(
            db_pool,
            limit=limit,
            offset=offset,
            direction=direction,
            endpoint=endpoint,
            session_id=session_id,
            status=status,
            model=model,
            after=after,
            before=before,
            search=search,
        )
    except Exception as exc:
        logger.error(f"Failed to list request logs: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


@router.get("/{transaction_id}", response_model=RequestLogDetailResponse)
async def get_transaction(
    transaction_id: str,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
) -> RequestLogDetailResponse:
    """Get all log entries for a single transaction (inbound + outbound)."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        return await get_transaction_logs(db_pool, transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to get transaction logs for {transaction_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


__all__ = ["router"]
