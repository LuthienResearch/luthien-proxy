"""UI routes for gateway activity monitoring and debugging."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from redis.asyncio import Redis

from luthien_proxy.dependencies import get_redis_client
from luthien_proxy.observability import stream_activity_events

router = APIRouter(prefix="", tags=["ui"])

# Static directory is relative to this module
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@router.get("/activity/stream")
async def activity_stream(
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
    """
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@router.get("/activity/monitor")
async def activity_monitor():
    """Activity monitor UI.

    Returns the HTML page for viewing the activity stream in real-time.
    """
    return FileResponse(os.path.join(STATIC_DIR, "activity_monitor.html"))


@router.get("/debug/diff")
async def diff_viewer():
    """Diff viewer UI.

    Returns the HTML page for viewing policy diffs with side-by-side comparison.
    """
    return FileResponse(os.path.join(STATIC_DIR, "diff_viewer.html"))


@router.get("/policy-config")
async def policy_config():
    """Policy configuration UI.

    Returns the HTML page for configuring, enabling, and testing policies
    through a guided wizard interface.
    """
    return FileResponse(os.path.join(STATIC_DIR, "policy_config.html"))


__all__ = ["router"]
