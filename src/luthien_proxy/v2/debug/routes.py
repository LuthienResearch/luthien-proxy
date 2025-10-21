# ABOUTME: Debug REST endpoints for querying conversation events and computing diffs
# ABOUTME: Provides API for policy debugging - retrieve events, compute diffs, list recent calls

"""Debug routes for V2 gateway.

This module provides REST endpoints for debugging policy decisions:
- GET /v2/debug/calls/{call_id} - Retrieve all events for a call
- GET /v2/debug/calls/{call_id}/diff - Compute diff between original and final
- GET /v2/debug/calls - List recent calls
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

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


# === Models ===


class ConversationEventResponse(BaseModel):
    """Response model for a single conversation event."""

    call_id: str
    event_type: str
    sequence: int
    timestamp: str
    hook: str
    payload: dict[str, Any]


class CallEventsResponse(BaseModel):
    """Response model for all events for a call."""

    call_id: str
    events: list[ConversationEventResponse]
    tempo_trace_url: str | None


class MessageDiff(BaseModel):
    """Diff for a single message in request."""

    index: int
    role: str
    original_content: str
    final_content: str
    changed: bool


class RequestDiff(BaseModel):
    """Diff between original and final request."""

    model_changed: bool
    original_model: str | None
    final_model: str | None
    max_tokens_changed: bool
    original_max_tokens: int | None
    final_max_tokens: int | None
    messages: list[MessageDiff]


class ResponseDiff(BaseModel):
    """Diff between original and final response."""

    content_changed: bool
    original_content: str
    final_content: str
    finish_reason_changed: bool
    original_finish_reason: str | None
    final_finish_reason: str | None


class CallDiffResponse(BaseModel):
    """Complete diff response for a call."""

    call_id: str
    request: RequestDiff | None
    response: ResponseDiff | None
    tempo_trace_url: str | None


class CallListItem(BaseModel):
    """Summary of a single call."""

    call_id: str
    event_count: int
    latest_timestamp: str


class CallListResponse(BaseModel):
    """Response for list of recent calls."""

    calls: list[CallListItem]
    total: int


# === Helper Functions ===


def _build_tempo_url(call_id: str, grafana_url: str = "http://localhost:3000") -> str:
    """Build Grafana Tempo trace URL for a call_id."""
    # Tempo search by tag: luthien.call_id=<call_id>
    return f"{grafana_url}/explore?left=%5B%22now-1h%22,%22now%22,%22Tempo%22,%7B%22query%22:%22%7Bluthien.call_id%3D%5C%22{call_id}%5C%22%7D%22%7D%5D"


def _extract_message_content(msg: dict[str, Any]) -> str:
    """Extract text content from a message dict."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Handle content blocks (e.g., Anthropic format)
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return " ".join(text_parts)
    return str(content)


def _compute_request_diff(original: dict[str, Any], final: dict[str, Any]) -> RequestDiff:
    """Compute diff between original and final request."""
    # Compare model
    orig_model = original.get("model")
    final_model = final.get("model")
    model_changed = orig_model != final_model

    # Compare max_tokens
    orig_max_tokens = original.get("max_tokens")
    final_max_tokens = final.get("max_tokens")
    max_tokens_changed = orig_max_tokens != final_max_tokens

    # Compare messages
    orig_messages = original.get("messages", [])
    final_messages = final.get("messages", [])

    message_diffs: list[MessageDiff] = []
    max_len = max(len(orig_messages), len(final_messages))

    for i in range(max_len):
        orig_msg = orig_messages[i] if i < len(orig_messages) else {}
        final_msg = final_messages[i] if i < len(final_messages) else {}

        role = orig_msg.get("role") or final_msg.get("role") or "unknown"
        orig_content = _extract_message_content(orig_msg)
        final_content = _extract_message_content(final_msg)

        message_diffs.append(
            MessageDiff(
                index=i,
                role=role,
                original_content=orig_content,
                final_content=final_content,
                changed=(orig_content != final_content),
            )
        )

    return RequestDiff(
        model_changed=model_changed,
        original_model=orig_model,
        final_model=final_model,
        max_tokens_changed=max_tokens_changed,
        original_max_tokens=orig_max_tokens,
        final_max_tokens=final_max_tokens,
        messages=message_diffs,
    )


def _compute_response_diff(original: dict[str, Any], final: dict[str, Any]) -> ResponseDiff:
    """Compute diff between original and final response."""
    # Extract content from choices
    orig_content = ""
    final_content = ""

    orig_choices = original.get("choices", [])
    final_choices = final.get("choices", [])

    if orig_choices:
        orig_msg = orig_choices[0].get("message", {})
        orig_content = orig_msg.get("content", "")

    if final_choices:
        final_msg = final_choices[0].get("message", {})
        final_content = final_msg.get("content", "")

    # Extract finish_reason
    orig_finish_reason = None
    final_finish_reason = None

    if orig_choices:
        orig_finish_reason = orig_choices[0].get("finish_reason")

    if final_choices:
        final_finish_reason = final_choices[0].get("finish_reason")

    return ResponseDiff(
        content_changed=(orig_content != final_content),
        original_content=orig_content,
        final_content=final_content,
        finish_reason_changed=(orig_finish_reason != final_finish_reason),
        original_finish_reason=orig_finish_reason,
        final_finish_reason=final_finish_reason,
    )


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
        async with db_pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT call_id, event_type, sequence, payload, created_at
                FROM conversation_events
                WHERE call_id = $1
                ORDER BY sequence ASC
                """,
                call_id,
            )

        if not rows:
            raise HTTPException(status_code=404, detail=f"No events found for call_id: {call_id}")

        from datetime import datetime

        events = [
            ConversationEventResponse(
                call_id=str(row["call_id"]),
                event_type=str(row["event_type"]),
                sequence=int(row["sequence"]),  # type: ignore[arg-type]
                timestamp=row["created_at"].isoformat()  # type: ignore[union-attr]
                if isinstance(row["created_at"], datetime)
                else str(row["created_at"]),
                hook="",  # Not stored in schema
                payload=dict(row["payload"]) if isinstance(row["payload"], dict) else {},  # type: ignore[arg-type]
            )
            for row in rows
        ]

        return CallEventsResponse(
            call_id=call_id,
            events=events,
            tempo_trace_url=_build_tempo_url(call_id),
        )

    except HTTPException:
        raise
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
        async with db_pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT call_id, event_type, payload
                FROM conversation_events
                WHERE call_id = $1 AND event_type IN ('v2_request', 'v2_response')
                ORDER BY sequence ASC
                """,
                call_id,
            )

        if not rows:
            raise HTTPException(status_code=404, detail=f"No events found for call_id: {call_id}")

        # Parse events
        request_diff = None
        response_diff = None

        for row in rows:
            event_type = str(row["event_type"])
            payload = row["payload"]
            if not isinstance(payload, dict):
                continue

            if event_type == "v2_request":
                # payload has {data: {original: {...}, final: {...}}}
                data = payload.get("data", {})
                if not isinstance(data, dict):
                    continue
                original = data.get("original", {})
                final = data.get("final", {})
                if isinstance(original, dict) and isinstance(final, dict):
                    request_diff = _compute_request_diff(original, final)

            elif event_type == "v2_response":
                # payload has {response: {original: {...}, final: {...}}}
                response_data = payload.get("response", {})
                if not isinstance(response_data, dict):
                    continue
                original = response_data.get("original", {})
                final = response_data.get("final", {})
                if isinstance(original, dict) and isinstance(final, dict):
                    response_diff = _compute_response_diff(original, final)

        return CallDiffResponse(
            call_id=call_id,
            request=request_diff,
            response=response_diff,
            tempo_trace_url=_build_tempo_url(call_id),
        )

    except HTTPException:
        raise
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
        async with db_pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    call_id,
                    COUNT(*) as event_count,
                    MAX(created_at) as latest
                FROM conversation_events
                GROUP BY call_id
                ORDER BY latest DESC
                LIMIT $1
                """,
                limit,
            )

        from datetime import datetime

        calls = [
            CallListItem(
                call_id=str(row["call_id"]),
                event_count=int(row["event_count"]),  # type: ignore[arg-type]
                latest_timestamp=row["latest"].isoformat()  # type: ignore[union-attr]
                if isinstance(row["latest"], datetime)
                else str(row["latest"]),
            )
            for row in rows
        ]

        return CallListResponse(
            calls=calls,
            total=len(calls),
        )

    except Exception as exc:
        logger.error(f"Failed to list recent calls: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


__all__ = ["router"]
