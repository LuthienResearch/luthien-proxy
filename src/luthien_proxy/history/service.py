"""Service layer for conversation history functionality.

Provides pure business logic for:
- Fetching session lists with summaries
- Fetching full session details with conversation turns
- Exporting sessions to markdown format
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool

from .models import (
    ConversationMessage,
    ConversationTurn,
    MessageType,
    PolicyAnnotation,
    SessionDetail,
    SessionListResponse,
    SessionSummary,
)

# User-friendly descriptions for common policy event types
_EVENT_TYPE_DESCRIPTIONS: dict[str, str] = {
    # Judge policy events
    "policy.judge.tool_call_allowed": "Tool call approved",
    "policy.judge.tool_call_blocked": "Tool call blocked",
    "policy.judge.evaluation_started": "Policy evaluation started",
    "policy.judge.evaluation_complete": "Policy evaluation complete",
    "policy.judge.evaluation_failed": "Policy evaluation failed",
    # Simple judge events
    "policy.simple_judge.request_evaluated": "Request evaluated",
    "policy.simple_judge.response_evaluated": "Response evaluated",
    "policy.simple_judge.tool_call_evaluated": "Tool call evaluated",
    # All caps policy events
    "policy.all_caps.content_transformed": "Content transformed to uppercase",
    "policy.all_caps.content_delta_warning": "Lowercase content detected",
    "policy.all_caps.tool_call_delta_warning": "Tool call content warning",
    "policy.all_caps.response_content_warning": "Response content warning",
    "policy.all_caps.response_content_transformed": "Response transformed",
    # Simple policy events
    "policy.simple_policy.content_complete_warning": "Content warning",
    "policy.simple_policy.tool_call_complete_warning": "Tool call warning",
}


def _get_event_summary(event_type: str, payload: dict[str, Any] | None) -> str:
    """Get a user-friendly summary for a policy event.

    Uses explicit summary from payload if available, falls back to
    pre-defined descriptions, then to the raw event type.
    """
    if payload and payload.get("summary"):
        return payload["summary"]
    return _EVENT_TYPE_DESCRIPTIONS.get(event_type, event_type)


def _extract_text_content(content: Any) -> str:
    """Extract text from message content (handles string or content blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Handle content blocks (Anthropic/OpenAI format)
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    # Tool use block - handled separately
                    pass
                elif block.get("type") == "tool_result":
                    # Tool result block - content can be string or array of blocks
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        parts.append(result_content)
                    elif isinstance(result_content, list):
                        # Recursively extract text from content blocks
                        for sub_block in result_content:
                            if isinstance(sub_block, dict) and sub_block.get("type") == "text":
                                parts.append(sub_block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _extract_tool_calls(message: dict[str, Any]) -> list[ConversationMessage]:
    """Extract tool calls from a message."""
    tool_messages = []

    # Check for OpenAI-style tool_calls
    tool_calls = message.get("tool_calls") or []
    for tc in tool_calls:
        if isinstance(tc, dict):
            func = tc.get("function", {})
            tool_messages.append(
                ConversationMessage(
                    message_type=MessageType.TOOL_CALL,
                    content=func.get("arguments", "{}"),
                    tool_name=func.get("name"),
                    tool_call_id=tc.get("id"),
                    tool_input=_safe_parse_json(func.get("arguments", "{}")),
                )
            )

    # Check for Anthropic-style content blocks with tool_use
    content = message.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_messages.append(
                    ConversationMessage(
                        message_type=MessageType.TOOL_CALL,
                        content=str(block.get("input", {})),
                        tool_name=block.get("name"),
                        tool_call_id=block.get("id"),
                        tool_input=block.get("input"),
                    )
                )

    return tool_messages


def _safe_parse_json(s: str) -> dict[str, Any] | None:
    """Safely parse JSON string, returning None on failure."""
    try:
        result = json.loads(s)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_request_messages(request: dict[str, Any]) -> list[ConversationMessage]:
    """Parse messages from a request payload."""
    messages = []
    raw_messages = request.get("messages", [])

    for msg in raw_messages:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "unknown")
        content = _extract_text_content(msg.get("content"))

        # Map role to message type
        if role == "system":
            msg_type = MessageType.SYSTEM
        elif role == "user":
            msg_type = MessageType.USER
        elif role == "assistant":
            msg_type = MessageType.ASSISTANT
        elif role == "tool":
            msg_type = MessageType.TOOL_RESULT
        else:
            msg_type = MessageType.USER  # Default

        # For tool results, include the tool_call_id
        tool_call_id = msg.get("tool_call_id") if role == "tool" else None

        # For assistant messages, extract any tool calls first
        if role == "assistant":
            tool_call_msgs = _extract_tool_calls(msg)
            if tool_call_msgs:
                # Add tool calls, then optionally add text content if present
                messages.extend(tool_call_msgs)
                if content:
                    messages.append(
                        ConversationMessage(
                            message_type=msg_type,
                            content=content,
                        )
                    )
                continue

        messages.append(
            ConversationMessage(
                message_type=msg_type,
                content=content,
                tool_call_id=tool_call_id,
            )
        )

    return messages


def _parse_response_messages(response: dict[str, Any]) -> list[ConversationMessage]:
    """Parse messages from a response payload."""
    messages = []
    choices = response.get("choices", [])

    for choice in choices:
        if not isinstance(choice, dict):
            continue

        msg = choice.get("message", {})
        if not isinstance(msg, dict):
            continue

        content = _extract_text_content(msg.get("content"))

        # Add the main assistant message if there's text content
        if content:
            messages.append(
                ConversationMessage(
                    message_type=MessageType.ASSISTANT,
                    content=content,
                )
            )

        # Extract tool calls
        tool_calls = _extract_tool_calls(msg)
        messages.extend(tool_calls)

    return messages


def _check_modifications(original: dict[str, Any], final: dict[str, Any]) -> tuple[bool, str | None]:
    """Check if content was modified between original and final."""
    orig_content = _extract_text_content(original.get("content"))
    final_content = _extract_text_content(final.get("content"))

    if orig_content != final_content:
        return True, orig_content
    return False, None


# Maximum length for first user message preview
_FIRST_MESSAGE_MAX_LENGTH = 100


def _extract_preview_message(payload: Any) -> str | None:
    """Extract the most recent user message from a request payload for preview.

    Used to generate a session preview/title. Returns truncated text.
    Note: Extracts the LAST user message (most recent context), not the first.
    """
    if not payload:
        return None

    # Handle JSON string (from asyncpg)
    if isinstance(payload, str):
        payload = _safe_parse_json(payload)
        if not payload:
            return None

    # Get the final request (prefer this as it's what was actually sent)
    request = payload.get("final_request") or payload.get("original_request") or {}
    messages = request.get("messages", [])

    # Messages to skip as they're not meaningful previews (Claude Code internals)
    _SKIP_MESSAGES = {"count", ""}

    # Find the last user message (most recent context)
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            content = _extract_text_content(msg.get("content"))
            if content:
                # Truncate and clean up for display
                content = content.strip()
                # Skip non-meaningful messages (like Claude Code's token counting)
                if content.lower() in _SKIP_MESSAGES:
                    continue
                # Replace newlines with spaces for single-line preview
                content = " ".join(content.split())
                if len(content) > _FIRST_MESSAGE_MAX_LENGTH:
                    content = content[:_FIRST_MESSAGE_MAX_LENGTH] + "..."
                return content

    return None


async def fetch_session_list(limit: int, db_pool: DatabasePool, offset: int = 0) -> SessionListResponse:
    """Fetch list of recent sessions with summaries.

    Args:
        limit: Maximum number of sessions to return
        db_pool: Database connection pool
        offset: Number of sessions to skip for pagination

    Returns:
        List of session summaries ordered by most recent activity
    """
    async with db_pool.connection() as conn:
        # Get total count of sessions
        total_count = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT session_id)
            FROM conversation_events
            WHERE session_id IS NOT NULL
            """
        )

        # Get session summaries with aggregated stats
        rows = await conn.fetch(
            """
            WITH session_stats AS (
                SELECT
                    session_id,
                    MIN(created_at) as first_ts,
                    MAX(created_at) as last_ts,
                    COUNT(*) as total_events,
                    COUNT(DISTINCT call_id) as turn_count,
                    COUNT(*) FILTER (
                        WHERE event_type LIKE 'policy.%'
                        AND event_type NOT LIKE 'policy.judge.evaluation%'
                    ) as policy_interventions
                FROM conversation_events
                WHERE session_id IS NOT NULL
                GROUP BY session_id
            ),
            session_models AS (
                SELECT DISTINCT
                    session_id,
                    payload->>'final_model' as model
                FROM conversation_events
                WHERE session_id IS NOT NULL
                AND event_type = 'transaction.request_recorded'
                AND payload->>'final_model' IS NOT NULL
            ),
            session_first_message AS (
                SELECT DISTINCT ON (session_id)
                    session_id,
                    payload as request_payload
                FROM conversation_events
                WHERE session_id IS NOT NULL
                AND event_type = 'transaction.request_recorded'
                -- Skip Claude Code token counting requests (just "count")
                AND COALESCE(payload->'final_request'->'messages'->0->>'content', '') != 'count'
                ORDER BY session_id, created_at ASC
            )
            SELECT
                s.session_id,
                s.first_ts,
                s.last_ts,
                s.total_events,
                s.turn_count,
                s.policy_interventions,
                COALESCE(
                    array_agg(DISTINCT m.model) FILTER (WHERE m.model IS NOT NULL),
                    ARRAY[]::text[]
                ) as models,
                f.request_payload
            FROM session_stats s
            LEFT JOIN session_models m ON s.session_id = m.session_id
            LEFT JOIN session_first_message f ON s.session_id = f.session_id
            GROUP BY s.session_id, s.first_ts, s.last_ts,
                     s.total_events, s.turn_count, s.policy_interventions,
                     f.request_payload
            ORDER BY s.last_ts DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

    sessions = [
        SessionSummary(
            session_id=str(row["session_id"]),
            first_timestamp=row["first_ts"].isoformat()
            if isinstance(row["first_ts"], datetime)
            else str(row["first_ts"]),
            last_timestamp=row["last_ts"].isoformat() if isinstance(row["last_ts"], datetime) else str(row["last_ts"]),
            turn_count=int(row["turn_count"]),  # type: ignore[arg-type]
            total_events=int(row["total_events"]),  # type: ignore[arg-type]
            policy_interventions=int(row["policy_interventions"]),  # type: ignore[arg-type]
            models_used=list(row["models"]) if row["models"] else [],  # type: ignore[arg-type]
            first_user_message=_extract_preview_message(row["request_payload"]),
        )
        for row in rows
    ]

    total = int(total_count) if total_count is not None else 0  # type: ignore[arg-type]
    has_more = offset + len(sessions) < total

    return SessionListResponse(sessions=sessions, total=total, offset=offset, has_more=has_more)


async def fetch_session_detail(session_id: str, db_pool: DatabasePool) -> SessionDetail:
    """Fetch full session detail with conversation turns.

    Args:
        session_id: Session identifier
        db_pool: Database connection pool

    Returns:
        Full session detail with all conversation turns

    Raises:
        ValueError: If no events found for session_id
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT call_id, event_type, payload, created_at
            FROM conversation_events
            WHERE session_id = $1
            ORDER BY created_at ASC
            """,
            session_id,
        )

    if not rows:
        raise ValueError(f"No events found for session_id: {session_id}")

    # Group events by call_id
    calls: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        call_id = str(row["call_id"])
        if call_id not in calls:
            calls[call_id] = []

        # Parse payload - asyncpg returns JSONB as string
        raw_payload = row["payload"]
        if isinstance(raw_payload, dict):
            payload = raw_payload
        elif isinstance(raw_payload, str):
            payload = _safe_parse_json(raw_payload) or {}
        else:
            payload = {}

        calls[call_id].append(
            {
                "event_type": str(row["event_type"]),
                "payload": payload,
                "created_at": row["created_at"],
            }
        )

    # Build conversation turns, sorted by first event timestamp
    turns = []
    all_models = set()
    total_interventions = 0

    # Sort call_ids by their first event timestamp to ensure chronological order
    sorted_call_ids = sorted(calls.keys(), key=lambda cid: calls[cid][0]["created_at"])
    for call_id in sorted_call_ids:
        turn = _build_turn(call_id, calls[call_id])
        turns.append(turn)
        if turn.model:
            all_models.add(turn.model)
        if turn.had_policy_intervention:
            total_interventions += len(turn.annotations)

    # Get timestamps
    first_ts = rows[0]["created_at"]
    last_ts = rows[-1]["created_at"]

    return SessionDetail(
        session_id=session_id,
        first_timestamp=first_ts.isoformat() if isinstance(first_ts, datetime) else str(first_ts),
        last_timestamp=last_ts.isoformat() if isinstance(last_ts, datetime) else str(last_ts),
        turns=turns,
        total_policy_interventions=total_interventions,
        models_used=sorted(all_models),
    )


def _build_turn(call_id: str, events: list[dict[str, Any]]) -> ConversationTurn:
    """Build a conversation turn from a list of events for a call."""
    request_messages: list[ConversationMessage] = []
    response_messages: list[ConversationMessage] = []
    annotations: list[PolicyAnnotation] = []
    model: str | None = None
    timestamp: str = ""
    had_intervention = False

    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        created_at = event["created_at"]

        if not timestamp and created_at:
            timestamp = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)

        # Handle request recorded
        if event_type == "transaction.request_recorded":
            model = payload.get("final_model")
            original_req = payload.get("original_request", {})
            final_req = payload.get("final_request", {})

            # Parse messages from final request
            request_messages = _parse_request_messages(final_req)

            # Check for modifications
            orig_messages = original_req.get("messages", [])
            final_messages = final_req.get("messages", [])
            for i, msg in enumerate(request_messages):
                if i < len(orig_messages) and i < len(final_messages):
                    was_modified, orig_content = _check_modifications(orig_messages[i], final_messages[i])
                    if was_modified:
                        msg.was_modified = True
                        msg.original_content = orig_content
                        had_intervention = True

        # Handle response recorded (streaming or non-streaming)
        elif event_type in (
            "transaction.streaming_response_recorded",
            "transaction.non_streaming_response_recorded",
        ):
            final_resp = payload.get("final_response", {})
            response_messages = _parse_response_messages(final_resp)

            # Check for response modifications
            original_resp = payload.get("original_response", {})
            orig_choices = original_resp.get("choices", [])
            final_choices = final_resp.get("choices", [])
            if orig_choices and final_choices:
                orig_msg = orig_choices[0].get("message", {})
                final_msg = final_choices[0].get("message", {})
                was_modified, orig_content = _check_modifications(orig_msg, final_msg)
                if was_modified and response_messages:
                    response_messages[0].was_modified = True
                    response_messages[0].original_content = orig_content
                    had_intervention = True

        # Handle policy events
        elif event_type.startswith("policy."):
            # Skip evaluation started/complete events
            if "evaluation" in event_type:
                continue

            annotations.append(
                PolicyAnnotation(
                    policy_name=_extract_policy_name(event_type),
                    event_type=event_type,
                    summary=_get_event_summary(event_type, payload),
                    details=payload if payload else None,
                )
            )
            had_intervention = True

    return ConversationTurn(
        call_id=call_id,
        timestamp=timestamp,
        model=model,
        request_messages=request_messages,
        response_messages=response_messages,
        annotations=annotations,
        had_policy_intervention=had_intervention,
    )


def _extract_policy_name(event_type: str) -> str:
    """Extract policy name from event type like 'policy.judge.tool_call_blocked'."""
    parts = event_type.split(".")
    if len(parts) >= 2:
        return parts[1]  # e.g., "judge" from "policy.judge.tool_call_blocked"
    return "unknown"


def export_session_markdown(session: SessionDetail) -> str:
    """Export a session to markdown format.

    Args:
        session: Session detail to export

    Returns:
        Markdown formatted string of the conversation
    """
    lines = []

    # Header
    lines.append(f"# Conversation History: {session.session_id}")
    lines.append("")
    lines.append(f"**Started:** {session.first_timestamp}")
    lines.append(f"**Ended:** {session.last_timestamp}")
    lines.append(f"**Turns:** {len(session.turns)}")
    if session.models_used:
        lines.append(f"**Models:** {', '.join(session.models_used)}")
    if session.total_policy_interventions > 0:
        lines.append(f"**Policy Interventions:** {session.total_policy_interventions}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Turns
    for i, turn in enumerate(session.turns, 1):
        lines.append(f"## Turn {i}")
        if turn.model:
            lines.append(f"*Model: {turn.model}*")
        lines.append("")

        # Request messages
        for msg in turn.request_messages:
            lines.append(_format_message_markdown(msg))
            lines.append("")

        # Response messages
        for msg in turn.response_messages:
            lines.append(_format_message_markdown(msg))
            lines.append("")

        # Policy annotations
        if turn.annotations:
            lines.append("### Policy Annotations")
            for ann in turn.annotations:
                lines.append(f"- **{ann.policy_name}**: {ann.summary}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _format_message_markdown(msg: ConversationMessage) -> str:
    """Format a single message as markdown."""
    type_labels = {
        MessageType.SYSTEM: "System",
        MessageType.USER: "User",
        MessageType.ASSISTANT: "Assistant",
        MessageType.TOOL_CALL: "Tool Call",
        MessageType.TOOL_RESULT: "Tool Result",
    }

    label = type_labels.get(msg.message_type, "Message")
    lines = [f"### {label}"]

    if msg.message_type == MessageType.TOOL_CALL and msg.tool_name:
        lines.append(f"**Tool:** `{msg.tool_name}`")
        if msg.tool_input:
            lines.append("```json")
            lines.append(json.dumps(msg.tool_input, indent=2))
            lines.append("```")
    elif msg.message_type == MessageType.TOOL_RESULT:
        if msg.tool_call_id:
            lines.append(f"*Response to: {msg.tool_call_id}*")
        lines.append("")
        lines.append(msg.content)
    else:
        lines.append("")
        lines.append(msg.content)

    if msg.was_modified:
        lines.append("")
        lines.append("> **Modified by policy**")
        if msg.original_content:
            lines.append("> Original content:")
            lines.append("> ```")
            original_lines = msg.original_content.split("\n")
            for line in original_lines[:5]:
                lines.append(f"> {line}")
            if len(original_lines) > 5:
                lines.append("> ...")
            lines.append("> ```")

    return "\n".join(lines)


__all__ = [
    "fetch_session_list",
    "fetch_session_detail",
    "export_session_markdown",
]
