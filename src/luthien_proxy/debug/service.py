"""Service layer for V2 debug functionality.

This module contains pure business logic for:
- Fetching conversation events from database
- Computing diffs between original and final requests/responses
- Listing recent calls

These functions are designed to be easily testable without FastAPI dependencies.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool

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

# === URL Building ===


def build_tempo_url(call_id: str, grafana_url: str | None = None) -> str:
    """Build Grafana Tempo trace URL for a call_id.

    Args:
        call_id: Unique identifier for the request/response cycle
        grafana_url: Base URL for Grafana instance (defaults to settings.grafana_url)

    Returns:
        URL-encoded Grafana Tempo search URL
    """
    if grafana_url is None:
        grafana_url = get_settings().grafana_url
    # TraceQL query: { span."luthien.call_id" = "call_id_value" }
    # The attribute name contains a dot, so it must be quoted per TraceQL syntax.
    # We also need the span. scope prefix for span attributes.
    traceql_query = f'{{ span."luthien.call_id" = "{call_id}" }}'

    # Build the Grafana Explore left pane JSON structure
    left_pane = {
        "datasource": "tempo",
        "queries": [{"refId": "A", "queryType": "traceql", "query": traceql_query}],
        "range": {"from": "now-1h", "to": "now"},
    }
    # URL-encode the entire JSON to avoid issues with double quotes breaking URLs
    encoded_left = urllib.parse.quote(json.dumps(left_pane), safe="")
    return f"{grafana_url}/explore?orgId=1&left={encoded_left}"


# === Message Content Extraction ===


def extract_message_content(msg: dict[str, Any]) -> str:
    """Extract text content from a message dict.

    Handles both simple string content and structured content blocks
    (e.g., Anthropic format with type: "text" blocks).

    Args:
        msg: Message dictionary with 'content' field

    Returns:
        Extracted text content as string
    """
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


# === Diff Computation ===


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


def compute_response_diff(original: dict[str, Any], final: dict[str, Any]) -> ResponseDiff:
    """Compute diff between original and final response.

    Compares:
    - message content from first choice
    - finish_reason from first choice

    Args:
        original: Original response payload
        final: Final response payload (after policy modifications)

    Returns:
        Structured diff showing what changed
    """
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


# === Event Fetching ===


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
            SELECT call_id, event_type, payload, created_at
            FROM conversation_events
            WHERE call_id = $1
            ORDER BY created_at ASC
            """,
            call_id,
        )

    if not rows:
        raise ValueError(f"No events found for call_id: {call_id}")

    events = [
        ConversationEventResponse(
            call_id=str(row["call_id"]),
            event_type=str(row["event_type"]),
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
        tempo_trace_url=build_tempo_url(call_id),
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
            WHERE call_id = $1 AND event_type IN ('v2_request', 'v2_response')
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
                request_diff = compute_request_diff(original, final)

        elif event_type == "v2_response":
            # payload has {response: {original: {...}, final: {...}}}
            response_data = payload.get("response", {})
            if not isinstance(response_data, dict):
                continue
            original = response_data.get("original", {})
            final = response_data.get("final", {})
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
                MAX(created_at) as latest
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


__all__ = [
    "build_tempo_url",
    "extract_message_content",
    "compute_request_diff",
    "compute_response_diff",
    "fetch_call_events",
    "fetch_call_diff",
    "fetch_recent_calls",
]
