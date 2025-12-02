# ABOUTME: Business logic for debug endpoints - testable functions for querying/computing diffs
# ABOUTME: Pure functions that don't depend on FastAPI - used by routes.py

"""Service layer for V2 debug functionality.

This module contains pure business logic for:
- Fetching conversation events from database
- Computing diffs between original and final requests/responses
- Listing recent calls

These functions are designed to be easily testable without FastAPI dependencies.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool

from .models import (
    CallDiffResponse,
    CallEventsResponse,
    CallListItem,
    CallListResponse,
    ConversationEventResponse,
    MessageDiff,
    RequestDiff,
    ResponseDiff,
    SpanData,
    TimelineEvent,
    TraceResponse,
)

# === URL Building ===


def build_tempo_url(call_id: str, grafana_url: str = "http://localhost:3000") -> str:
    """Build Grafana Tempo trace URL for a call_id.

    Args:
        call_id: Unique identifier for the request/response cycle
        grafana_url: Base URL for Grafana instance

    Returns:
        URL-encoded Grafana Tempo search URL
    """
    # Tempo search by tag: luthien.call_id=<call_id>
    return f"{grafana_url}/explore?left=%5B%22now-1h%22,%22now%22,%22Tempo%22,%7B%22query%22:%22%7Bluthien.call_id%3D%5C%22{call_id}%5C%22%7D%22%7D%5D"


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
            SELECT call_id, event_type, sequence, payload, created_at
            FROM conversation_events
            WHERE call_id = $1
            ORDER BY sequence ASC
            """,
            call_id,
        )

    if not rows:
        raise ValueError(f"No events found for call_id: {call_id}")

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
            ORDER BY sequence ASC
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


# === Trace Viewer Functions ===


def build_grafana_logs_url(
    call_id: str,
    grafana_url: str = "http://localhost:3000",
    start_time: str | None = None,
    end_time: str | None = None,
) -> str:
    """Build Grafana Loki logs URL for a call_id.

    Args:
        call_id: Unique identifier for the request/response cycle
        grafana_url: Base URL for Grafana instance
        start_time: Optional start time for log query
        end_time: Optional end time for log query

    Returns:
        URL-encoded Grafana Loki explore URL
    """
    # Loki query to find logs with call_id in message or json fields
    query = f'{{service_name="luthien-proxy"}} |= "{call_id}"'
    encoded_query = urllib.parse.quote(query)
    return f"{grafana_url}/explore?left=%5B%22now-1h%22,%22now%22,%22Loki%22,%7B%22expr%22:%22{encoded_query}%22%7D%5D"


def _categorize_event_type(event_type: str) -> str:
    """Categorize an event type for timeline grouping.

    Args:
        event_type: Raw event type from database

    Returns:
        Category string: request, response, policy, streaming, or system
    """
    event_lower = event_type.lower()
    if "request" in event_lower:
        return "request"
    if "response" in event_lower:
        return "response"
    if "policy" in event_lower:
        return "policy"
    if "stream" in event_lower:
        return "streaming"
    return "system"


def _format_event_title(event_type: str, payload: dict[str, Any]) -> str:
    """Generate a human-readable title for a timeline event.

    Args:
        event_type: Event type from database
        payload: Event payload containing details

    Returns:
        Human-readable title string
    """
    # Map common event types to readable titles
    title_map = {
        "v2_request": "Request Received",
        "v2_response": "Response Generated",
        "request": "Request",
        "response": "Response",
    }

    if event_type in title_map:
        title = title_map[event_type]
        # Add model info if available
        data = payload.get("data", {})
        if isinstance(data, dict):
            model = data.get("final", {}).get("model") or data.get("original", {}).get("model")
            if model:
                title = f"{title} ({model})"
        return title

    # For policy events, extract policy name
    if "policy" in event_type.lower():
        policy_class = payload.get("policy_class", "")
        if policy_class:
            # Extract just the class name
            policy_name = policy_class.split(":")[-1] if ":" in policy_class else policy_class.split(".")[-1]
            return f"Policy: {policy_name}"

    # Default: convert event_type to title case
    return event_type.replace("_", " ").title()


def _extract_event_description(event_type: str, payload: dict[str, Any]) -> str | None:
    """Extract a brief description from event payload.

    Args:
        event_type: Event type from database
        payload: Event payload

    Returns:
        Description string or None
    """
    if event_type == "v2_request":
        data = payload.get("data", {})
        if isinstance(data, dict):
            original = data.get("original", {})
            if isinstance(original, dict):
                messages = original.get("messages", [])
                if messages and isinstance(messages, list):
                    # Get last user message preview
                    for msg in reversed(messages):
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str) and content:
                                return content[:100] + ("..." if len(content) > 100 else "")
    elif event_type == "v2_response":
        response = payload.get("response", {})
        if isinstance(response, dict):
            final = response.get("final", {})
            if isinstance(final, dict):
                choices = final.get("choices", [])
                if choices and isinstance(choices, list):
                    first_choice = choices[0]
                    if isinstance(first_choice, dict):
                        message = first_choice.get("message", {})
                        if isinstance(message, dict):
                            content = message.get("content", "")
                            if isinstance(content, str) and content:
                                return content[:100] + ("..." if len(content) > 100 else "")
    return None


async def fetch_call_trace(call_id: str, db_pool: DatabasePool) -> TraceResponse:
    """Fetch complete trace data for a call from database.

    This function queries the database for conversation events and policy events,
    then builds a hierarchical trace structure suitable for timeline visualization.

    Args:
        call_id: Unique identifier for the request/response cycle
        db_pool: Database connection pool

    Returns:
        Complete trace data including spans, logs, and timeline events

    Raises:
        ValueError: If no events found for call_id
    """
    # Fetch conversation events
    async with db_pool.connection() as conn:
        conv_rows = await conn.fetch(
            """
            SELECT id, call_id, event_type, sequence, payload, created_at
            FROM conversation_events
            WHERE call_id = $1
            ORDER BY sequence ASC
            """,
            call_id,
        )

        # Fetch policy events
        policy_rows = await conn.fetch(
            """
            SELECT id, call_id, policy_class, policy_config, event_type,
                   metadata, created_at
            FROM policy_events
            WHERE call_id = $1
            ORDER BY created_at ASC
            """,
            call_id,
        )

        # Fetch call metadata
        call_row = await conn.fetchrow(
            """
            SELECT call_id, model_name, provider, status, created_at, completed_at
            FROM conversation_calls
            WHERE call_id = $1
            """,
            call_id,
        )

    if not conv_rows and not policy_rows:
        raise ValueError(f"No events found for call_id: {call_id}")

    # Build timeline events from conversation events
    timeline_events: list[TimelineEvent] = []
    timestamps: list[datetime] = []

    for row in conv_rows:
        event_id = str(row["id"])
        event_type = str(row["event_type"])
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        created_at = row["created_at"]

        if isinstance(created_at, datetime):
            timestamps.append(created_at)
            timestamp_str = created_at.isoformat()
        else:
            timestamp_str = str(created_at)

        timeline_events.append(
            TimelineEvent(
                id=event_id,
                timestamp=timestamp_str,
                event_type=event_type,
                category=_categorize_event_type(event_type),
                title=_format_event_title(event_type, payload),
                description=_extract_event_description(event_type, payload),
                payload=payload,
            )
        )

    # Build timeline events from policy events
    for row in policy_rows:
        event_id = str(row["id"])
        event_type = str(row["event_type"])
        policy_class = str(row["policy_class"]) if row["policy_class"] else ""
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        created_at = row["created_at"]

        if isinstance(created_at, datetime):
            timestamps.append(created_at)
            timestamp_str = created_at.isoformat()
        else:
            timestamp_str = str(created_at)

        # Extract policy name from class path
        policy_name = policy_class.split(":")[-1] if ":" in policy_class else policy_class.split(".")[-1]

        timeline_events.append(
            TimelineEvent(
                id=event_id,
                timestamp=timestamp_str,
                event_type=event_type,
                category="policy",
                title=f"Policy: {policy_name}",
                description=metadata.get("action") or metadata.get("result"),
                payload={"policy_class": policy_class, "metadata": metadata},
            )
        )

    # Sort all timeline events by timestamp
    timeline_events.sort(key=lambda e: e.timestamp)

    # Calculate trace timing
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: float | None = None

    if timestamps:
        min_ts = min(timestamps)
        max_ts = max(timestamps)
        start_time = min_ts.isoformat()
        end_time = max_ts.isoformat()
        duration_ms = (max_ts - min_ts).total_seconds() * 1000

    # Build synthetic spans representing the request lifecycle
    spans: list[SpanData] = []

    # Root span for the entire call
    root_span_id = f"root-{call_id[:8]}"
    spans.append(
        SpanData(
            span_id=root_span_id,
            parent_span_id=None,
            name="API Request",
            start_time=start_time or "",
            end_time=end_time,
            duration_ms=duration_ms,
            status="ok",
            kind="server",
            attributes={"call_id": call_id},
        )
    )

    # Request processing span
    request_events = [e for e in timeline_events if e.category == "request"]
    if request_events:
        req_span_id = f"request-{call_id[:8]}"
        spans.append(
            SpanData(
                span_id=req_span_id,
                parent_span_id=root_span_id,
                name="Request Processing",
                start_time=request_events[0].timestamp,
                end_time=request_events[-1].timestamp if len(request_events) > 1 else request_events[0].timestamp,
                status="ok",
                kind="internal",
                attributes={"event_count": len(request_events)},
            )
        )

    # Policy processing span (if there are policy events)
    policy_events = [e for e in timeline_events if e.category == "policy"]
    if policy_events:
        policy_span_id = f"policy-{call_id[:8]}"
        policy_start = min(e.timestamp for e in policy_events)
        policy_end = max(e.timestamp for e in policy_events)
        spans.append(
            SpanData(
                span_id=policy_span_id,
                parent_span_id=root_span_id,
                name="Policy Evaluation",
                start_time=policy_start,
                end_time=policy_end,
                status="ok",
                kind="internal",
                attributes={
                    "policy_count": len(policy_events),
                    "policies": list({e.title for e in policy_events}),
                },
            )
        )

    # Response processing span
    response_events = [e for e in timeline_events if e.category == "response"]
    if response_events:
        resp_span_id = f"response-{call_id[:8]}"
        spans.append(
            SpanData(
                span_id=resp_span_id,
                parent_span_id=root_span_id,
                name="Response Generation",
                start_time=response_events[0].timestamp,
                end_time=response_events[-1].timestamp if len(response_events) > 1 else response_events[0].timestamp,
                status="ok",
                kind="internal",
                attributes={"event_count": len(response_events)},
            )
        )

    # Extract metadata from call row
    model_name: str | None = None
    provider: str | None = None
    status: str = "unknown"

    if call_row:
        raw_model = call_row["model_name"]
        raw_provider = call_row["provider"]
        raw_status = call_row["status"]
        model_name = str(raw_model) if raw_model else None
        provider = str(raw_provider) if raw_provider else None
        status = str(raw_status) if raw_status else "unknown"

    return TraceResponse(
        call_id=call_id,
        trace_id=None,  # Would need actual trace ID from OpenTelemetry
        start_time=start_time,
        end_time=end_time,
        duration_ms=duration_ms,
        status=status,
        model=model_name,
        provider=provider,
        spans=spans,
        logs=[],  # Logs would come from Loki in production
        timeline_events=timeline_events,
        tempo_trace_url=build_tempo_url(call_id),
        grafana_logs_url=build_grafana_logs_url(call_id),
    )


__all__ = [
    "build_tempo_url",
    "build_grafana_logs_url",
    "extract_message_content",
    "compute_request_diff",
    "compute_response_diff",
    "fetch_call_events",
    "fetch_call_diff",
    "fetch_recent_calls",
    "fetch_call_trace",
]
