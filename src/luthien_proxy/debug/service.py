"""Service layer for V2 debug functionality.

This module contains pure business logic for:
- Fetching conversation events from database
- Computing diffs between original and final requests/responses
- Listing recent calls

These functions are designed to be easily testable without FastAPI dependencies.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import TYPE_CHECKING, Any

from luthien_proxy.utils.db import parse_db_ts

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool

from luthien_proxy.history.service import extract_text_content
from luthien_proxy.settings import get_settings

from .models import (
    CallDiffResponse,
    CallEventsResponse,
    CallListItem,
    CallListResponse,
    ConversationEventResponse,
    MessageDiff,
    RequestDiff,
    ResponseDiff,
)

logger = logging.getLogger(__name__)


def _parse_payload(raw: object) -> dict[str, Any]:
    """Parse a JSONB payload from asyncpg (may arrive as dict or str)."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return json.loads(raw)  # type: ignore[no-any-return]
    logger.warning("Unexpected payload type from database: %s", type(raw).__name__)
    return {}


def build_tempo_url(call_id: str, tempo_url: str | None = None) -> str:
    """Build a Tempo API search URL for a call_id.

    Args:
        call_id: Unique identifier for the request/response cycle
        tempo_url: Base URL for the Tempo HTTP API (defaults to settings.tempo_url)

    Returns:
        Tempo API search URL with a TraceQL query for this call_id
    """
    if tempo_url is None:
        tempo_url = get_settings().tempo_url
    traceql_query = f'{{ span."luthien.call_id" = "{call_id}" }}'
    encoded_query = urllib.parse.quote(traceql_query)
    return f"{tempo_url}/api/search?q={encoded_query}"


def extract_message_content(msg: dict[str, Any]) -> str:
    """Extract text content from a message dict.

    Delegates to the shared _extract_text_content for content block parsing.
    """
    return extract_text_content(msg.get("content", ""))


def compute_request_diff(original: dict[str, Any], final: dict[str, Any]) -> RequestDiff:
    """Compute diff between original and final request.

    Compares:
    - model parameter
    - max_tokens parameter
    - messages array (content changes)

    Args:
        original: Original request payload
        final: Final request payload (after policy modifications)

    Returns:
        Structured diff showing what changed
    """
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
        orig_content = extract_message_content(orig_msg)
        final_content = extract_message_content(final_msg)

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


def _extract_response_content(response: dict[str, Any]) -> str:
    """Extract text content from response, supporting historical stored data.

    Supports reading both OpenAI and Anthropic format responses for backwards
    compatibility with stored historical data.

    OpenAI format: choices[0].message.content
    Anthropic format: content[].text (joined from text blocks)
    """
    # OpenAI format (for backwards compat with stored data)
    choices = response.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        return str(msg.get("content", "") or "")

    # Anthropic format
    content_blocks = response.get("content", [])
    if content_blocks:
        texts = [block.get("text", "") for block in content_blocks if block.get("type") == "text"]
        return "\n".join(texts)

    return ""


def _extract_finish_reason(response: dict[str, Any]) -> str | None:
    """Extract finish reason from response, supporting historical stored data.

    Supports reading both OpenAI and Anthropic format responses for backwards
    compatibility with stored historical data.

    OpenAI format: choices[0].finish_reason
    Anthropic format: stop_reason
    """
    choices = response.get("choices", [])
    if choices:
        return choices[0].get("finish_reason")
    return response.get("stop_reason")


def compute_response_diff(original: dict[str, Any], final: dict[str, Any]) -> ResponseDiff:
    """Compute diff between original and final response.

    Compares:
    - message content (supports both OpenAI choices and Anthropic content blocks)
    - finish_reason / stop_reason

    Args:
        original: Original response payload
        final: Final response payload (after policy modifications)

    Returns:
        Structured diff showing what changed
    """
    orig_content = _extract_response_content(original)
    final_content = _extract_response_content(final)
    orig_finish_reason = _extract_finish_reason(original)
    final_finish_reason = _extract_finish_reason(final)

    return ResponseDiff(
        content_changed=(orig_content != final_content),
        original_content=orig_content,
        final_content=final_content,
        finish_reason_changed=(orig_finish_reason != final_finish_reason),
        original_finish_reason=orig_finish_reason,
        final_finish_reason=final_finish_reason,
    )


async def fetch_call_events(call_id: str, db_pool: DatabasePool) -> CallEventsResponse:
    """Fetch all conversation events for a call from database.

    Args:
        call_id: Unique identifier for the request/response cycle
        db_pool: Database connection pool

    Returns:
        All events for the call with Tempo trace URL

    Raises:
        ValueError: If no events found for call_id
        Exception: If database query fails
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT call_id, event_type, payload, created_at, session_id
            FROM conversation_events
            WHERE call_id = $1
            ORDER BY created_at ASC
            """,
            call_id,
        )

    if not rows:
        raise ValueError(f"No events found for call_id: {call_id}")

    # Get session_id from first event that has it
    call_session_id = None
    for row in rows:
        if row["session_id"]:
            call_session_id = str(row["session_id"])
            break

    events = [
        ConversationEventResponse(
            call_id=str(row["call_id"]),
            event_type=str(row["event_type"]),
            timestamp=parse_db_ts(row["created_at"]).isoformat(),
            hook="",  # Not stored in schema
            payload=_parse_payload(row["payload"]),
            session_id=str(row["session_id"]) if row["session_id"] else None,
        )
        for row in rows
    ]

    return CallEventsResponse(
        call_id=call_id,
        events=events,
        tempo_trace_url=build_tempo_url(call_id),
        session_id=call_session_id,
    )


async def fetch_call_diff(call_id: str, db_pool: DatabasePool) -> CallDiffResponse:
    """Fetch and compute diff between original and final request/response.

    Args:
        call_id: Unique identifier for the request/response cycle
        db_pool: Database connection pool

    Returns:
        Structured diff showing policy changes

    Raises:
        ValueError: If no events found for call_id
        Exception: If database query fails
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT call_id, event_type, payload
            FROM conversation_events
            WHERE call_id = $1 AND event_type IN (
                'transaction.request_recorded',
                'transaction.non_streaming_response_recorded',
                'transaction.streaming_response_recorded'
            )
            ORDER BY created_at ASC
            """,
            call_id,
        )

    if not rows:
        raise ValueError(f"No events found for call_id: {call_id}")

    # Parse events
    request_diff = None
    response_diff = None

    for row in rows:
        event_type = str(row["event_type"])
        payload = _parse_payload(row["payload"])
        if not payload:
            continue

        if event_type == "transaction.request_recorded":
            # payload has {original_request: {...}, final_request: {...}, ...}
            original = payload.get("original_request", {})
            final = payload.get("final_request", {})
            if isinstance(original, dict) and isinstance(final, dict):
                request_diff = compute_request_diff(original, final)

        elif event_type in (
            "transaction.non_streaming_response_recorded",
            "transaction.streaming_response_recorded",
        ):
            # payload has {original_response: {...}, final_response: {...}, ...}
            original = payload.get("original_response") or {}
            final = payload.get("final_response") or {}
            if isinstance(original, dict) and isinstance(final, dict):
                response_diff = compute_response_diff(original, final)

    return CallDiffResponse(
        call_id=call_id,
        request=request_diff,
        response=response_diff,
        tempo_trace_url=build_tempo_url(call_id),
    )


async def fetch_recent_calls(limit: int, db_pool: DatabasePool) -> CallListResponse:
    """Fetch recent calls with event counts.

    Args:
        limit: Maximum number of calls to return
        db_pool: Database connection pool

    Returns:
        List of recent calls ordered by latest timestamp

    Raises:
        Exception: If database query fails
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                call_id,
                COUNT(*) as event_count,
                MAX(created_at) as latest,
                MAX(session_id) as session_id
            FROM conversation_events
            GROUP BY call_id
            ORDER BY latest DESC
            LIMIT $1
            """,
            limit,
        )

    calls = [
        CallListItem(
            call_id=str(row["call_id"]),
            event_count=int(row["event_count"]),  # type: ignore[arg-type]
            latest_timestamp=parse_db_ts(row["latest"]).isoformat(),
            session_id=str(row["session_id"]) if row["session_id"] else None,
        )
        for row in rows
    ]

    return CallListResponse(
        calls=calls,
        total=len(calls),
    )


__all__ = [
    "build_tempo_url",
    "extract_message_content",
    "compute_request_diff",
    "compute_response_diff",
    "fetch_call_events",
    "fetch_call_diff",
    "fetch_recent_calls",
]
