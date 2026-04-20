"""Service layer for conversation history functionality.

Provides pure business logic for:
- Fetching session lists with summaries (with optional server-side search/filter)
- Fetching full session details with conversation turns
- Exporting sessions to markdown format
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, TypedDict, cast

from luthien_proxy.utils.db import DatabasePool, parse_db_ts

from .models import (
    ConversationMessage,
    ConversationTurn,
    MessageType,
    PolicyAnnotation,
    SessionDetail,
    SessionListResponse,
    SessionSearchParams,
    SessionSummary,
)

# DB row payload type: asyncpg returns dict, aiosqlite returns str, may be NULL
_PreviewPayload = dict[str, Any] | str | None


class StoredEvent(TypedDict):
    """Structure of an event retrieved from the database."""

    event_type: str
    payload: dict[str, Any]
    created_at: datetime


logger = logging.getLogger(__name__)

# User-friendly descriptions for common policy event types
_EVENT_TYPE_DESCRIPTIONS: dict[str, str] = {
    # Judge policy events
    "policy.judge.tool_call_allowed": "Tool call approved",
    "policy.judge.tool_call_blocked": "Tool call blocked",
    "policy.judge.evaluation_started": "Policy evaluation started",
    "policy.judge.evaluation_complete": "Policy evaluation complete",
    "policy.judge.evaluation_failed": "Policy evaluation failed",
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


def extract_text_content(content: str | list[dict[str, Any]] | None) -> str:
    """Extract text from message content.

    Args:
        content: Message content - either a string, list of content blocks, or None

    Returns:
        Extracted text as string
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    # Content is a list of content blocks
    parts: list[str] = []
    for block in content:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif block.get("type") == "tool_result":
            result_content = block.get("content")
            if result_content is not None:
                parts.append(extract_text_content(result_content))
        # Skip tool_use (handled by _extract_tool_calls) and other block types
    return "\n".join(parts)


def _extract_tool_calls(message: dict[str, Any]) -> list[ConversationMessage]:
    """Extract tool calls from a message.

    Handles both OpenAI-style tool_calls and Anthropic-style tool_use content blocks.
    """
    tool_messages: list[ConversationMessage] = []

    # OpenAI-style tool_calls
    tool_calls = message.get("tool_calls")
    if tool_calls is not None:
        for tc in tool_calls:
            func = tc.get("function", {})
            arguments = func.get("arguments", "{}")
            tool_messages.append(
                ConversationMessage(
                    message_type=MessageType.TOOL_CALL,
                    content=arguments,
                    tool_name=func.get("name"),
                    tool_call_id=tc.get("id"),
                    tool_input=_safe_parse_json(arguments),
                )
            )

    # Anthropic-style content blocks with tool_use
    content = message.get("content")
    if content is not None and isinstance(content, list):
        for block in content:
            if block.get("type") == "tool_use":
                tool_input_raw = block.get("input", {})
                tool_input: dict[str, object] = dict(tool_input_raw) if isinstance(tool_input_raw, dict) else {}
                tool_messages.append(
                    ConversationMessage(
                        message_type=MessageType.TOOL_CALL,
                        content=str(tool_input_raw),
                        tool_name=block.get("name"),
                        tool_call_id=block.get("id"),
                        tool_input=tool_input,
                    )
                )

    return tool_messages


def _safe_parse_json(s: str) -> dict[str, Any] | None:
    """Safely parse JSON string, returning None on failure."""
    try:
        result = json.loads(s)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug(f"JSON parse failed in _safe_parse_json: {repr(e)}")
        return None


_ROLE_TO_MESSAGE_TYPE: dict[str, MessageType] = {
    "system": MessageType.SYSTEM,
    "user": MessageType.USER,
    "assistant": MessageType.ASSISTANT,
    "tool": MessageType.TOOL_RESULT,
}


def _parse_request_messages(request: dict[str, Any]) -> list[ConversationMessage]:
    """Parse messages from a request payload."""
    messages: list[ConversationMessage] = []
    raw_messages = request.get("messages", [])

    for msg in raw_messages:
        role = msg.get("role", "")
        msg_type = _ROLE_TO_MESSAGE_TYPE.get(role, MessageType.UNKNOWN)
        if msg_type == MessageType.UNKNOWN:
            raise ValueError(f"Unrecognized message role: '{role}'")

        content = extract_text_content(msg.get("content"))

        # For OpenAI-style tool results, include the tool_call_id
        tool_call_id = msg.get("tool_call_id") if msg_type == MessageType.TOOL_RESULT else None

        # For assistant messages, extract any tool calls first
        if msg_type == MessageType.ASSISTANT:
            tool_call_msgs = _extract_tool_calls(msg)
            if tool_call_msgs:
                messages.extend(tool_call_msgs)
                if content:
                    messages.append(
                        ConversationMessage(
                            message_type=msg_type,
                            content=content,
                        )
                    )
                continue

        # Anthropic-style tool results: user messages with tool_result content blocks.
        # Split these into separate TOOL_RESULT messages with their tool_use_id
        # so the frontend can pair them with tool calls.
        if msg_type == MessageType.USER:
            raw_content = msg.get("content")
            if isinstance(raw_content, list):
                has_tool_results = any(b.get("type") == "tool_result" for b in raw_content)
                if has_tool_results:
                    for block in raw_content:
                        if block.get("type") == "tool_result":
                            result_content = block.get("content")
                            text = extract_text_content(result_content) if result_content is not None else ""
                            # False/None → None; only True propagates
                            is_error = block.get("is_error") or None
                            messages.append(
                                ConversationMessage(
                                    message_type=MessageType.TOOL_RESULT,
                                    content=text,
                                    tool_call_id=block.get("tool_use_id"),
                                    is_error=is_error,
                                )
                            )
                        elif block.get("type") == "text" and block.get("text", "").strip():
                            messages.append(
                                ConversationMessage(
                                    message_type=MessageType.USER,
                                    content=block["text"],
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
    """Parse messages from a response payload.

    Handles both OpenAI format (choices[].message) and Anthropic format
    (content blocks directly on the response with role at top level).
    """
    messages: list[ConversationMessage] = []

    # OpenAI format: response has "choices" list
    choices = response.get("choices", [])
    if choices:
        for choice in choices:
            msg = choice.get("message")
            if msg is None:
                continue

            content = extract_text_content(msg.get("content"))

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

    # Anthropic format: response has "role" and "content" at top level
    if response.get("role") == "assistant" and "content" in response:
        content = extract_text_content(response.get("content"))

        if content:
            messages.append(
                ConversationMessage(
                    message_type=MessageType.ASSISTANT,
                    content=content,
                )
            )

        # Extract tool calls from Anthropic content blocks
        tool_calls = _extract_tool_calls(response)
        messages.extend(tool_calls)

    return messages


# Maximum length for first user message preview
_FIRST_MESSAGE_MAX_LENGTH = 100

# Pattern to strip system-reminder tags from content
_SYSTEM_REMINDER_PATTERN = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.DOTALL)


def _extract_preview_message(payload: dict[str, Any] | str | None) -> str | None:
    """Extract the first meaningful user message from a request payload for preview.

    Used to generate a session preview/title. Returns truncated text.
    Skips system-reminders and other non-meaningful content to find actual user intent.
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

    # Skip probe requests structurally: Claude Code sends internal probes
    # (token counting, quota checks) with max_tokens=1. No real conversation
    # uses max_tokens=1, so this catches all probes without a content blocklist.
    max_tokens = request.get("max_tokens")
    if max_tokens is not None:
        try:
            if int(max_tokens) <= 1:
                return None
        except (TypeError, ValueError) as e:
            logger.debug(f"max_tokens conversion failed: {repr(e)}")

    messages = request.get("messages", [])

    # Find the first meaningful user message (captures session intent)
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            content = extract_text_content(msg.get("content"))
            if content:
                # Truncate and clean up for display
                content = content.strip()
                # Skip system-reminder tags (Claude Code injects these)
                if content.startswith("<system-reminder>"):
                    content = _SYSTEM_REMINDER_PATTERN.sub("", content).strip()
                if not content:
                    continue
                # Replace newlines with spaces for single-line preview
                content = " ".join(content.split())
                if len(content) > _FIRST_MESSAGE_MAX_LENGTH:
                    content = content[:_FIRST_MESSAGE_MAX_LENGTH] + "..."
                return content

    return None


async def fetch_session_list(
    limit: int,
    db_pool: DatabasePool,
    offset: int = 0,
    search: SessionSearchParams | None = None,
) -> SessionListResponse:
    """Fetch list of recent sessions with summaries.

    Args:
        limit: Maximum number of sessions to return
        db_pool: Database connection pool
        offset: Number of sessions to skip for pagination
        search: Optional search/filter parameters. When None or empty, returns
            all sessions (original behavior, fully backward compatible).

    Returns:
        List of session summaries ordered by most recent activity
    """
    if search is None:
        search = SessionSearchParams()

    params: list[Any] = []
    param_idx = 1

    def add_param(value: Any) -> str:
        nonlocal param_idx
        placeholder = f"${param_idx}"
        params.append(value)
        param_idx += 1
        return placeholder

    def escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def model_expr_on(alias: str) -> str:
        return (
            f"{alias}.payload->>'final_model'"
            if db_pool.is_postgres
            else f"json_extract({alias}.payload, '$.final_model')"
        )

    # Bare-column expressions for standalone queries (no table alias needed).
    model_expr = "payload->>'final_model'" if db_pool.is_postgres else "json_extract(payload, '$.final_model')"
    max_tokens_expr = (
        "payload->'final_request'->>'max_tokens'"
        if db_pool.is_postgres
        else "json_extract(payload, '$.final_request.max_tokens')"
    )
    policy_count_expr = (
        "COUNT(*) FILTER (WHERE ce.event_type LIKE 'policy.%' AND ce.event_type NOT LIKE 'policy.judge.evaluation%')"
        if db_pool.is_postgres
        else "SUM(CASE WHEN ce.event_type LIKE 'policy.%' AND ce.event_type NOT LIKE 'policy.judge.evaluation%' THEN 1 ELSE 0 END)"
    )

    qualifying_where = ["ce.session_id IS NOT NULL"]
    qualifying_having: list[str] = []

    if search.user is not None:
        qualifying_where.append(f"ce.session_id LIKE {add_param(escape_like(search.user) + '%')} ESCAPE '\\'")

    if search.model is not None:
        model_placeholder = add_param(search.model)
        qualifying_where.append(
            f"""
            EXISTS (
                SELECT 1
                FROM conversation_events ce_model
                WHERE ce_model.session_id = ce.session_id
                AND ce_model.event_type = 'transaction.request_recorded'
                AND ce_model.session_id IS NOT NULL
                AND {model_expr_on("ce_model")} = {model_placeholder}
            )
            """.strip()
        )

    if search.q is not None:
        if db_pool.is_postgres:
            q_placeholder = add_param(search.q)
            qualifying_where.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM conversation_events ce_search
                    WHERE ce_search.session_id = ce.session_id
                    AND ce_search.session_id IS NOT NULL
                    AND ce_search.search_vector @@ plainto_tsquery('english', {q_placeholder})
                )
                """.strip()
            )
        else:
            escaped_q = f"%{escape_like(search.q)}%"
            request_content_placeholder = add_param(escaped_q)
            request_block_placeholder = add_param(escaped_q)
            response_content_placeholder = add_param(escaped_q)
            qualifying_where.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM conversation_events ce_search
                    WHERE ce_search.session_id = ce.session_id
                    AND ce_search.session_id IS NOT NULL
                    AND ce_search.event_type = 'transaction.request_recorded'
                    AND (
                        EXISTS (
                            SELECT 1
                            FROM json_tree(ce_search.payload, '$.final_request.messages') jt
                            WHERE jt.type = 'text'
                            AND (
                                (
                                    jt.fullkey GLOB '$.final_request.messages[*].content'
                                    AND jt.value LIKE {request_content_placeholder} ESCAPE '\\'
                                    AND EXISTS (
                                        SELECT 1
                                        FROM json_tree(ce_search.payload, '$.final_request.messages') role_node
                                        WHERE role_node.fullkey = substr(jt.fullkey, 1, length(jt.fullkey) - length('.content')) || '.role'
                                        AND role_node.value = 'user'
                                    )
                                )
                                OR (
                                    jt.fullkey GLOB '$.final_request.messages[*].content[*].text'
                                    AND jt.value LIKE {request_block_placeholder} ESCAPE '\\'
                                    AND EXISTS (
                                        SELECT 1
                                        FROM json_tree(ce_search.payload, '$.final_request.messages') role_node
                                        WHERE role_node.fullkey = substr(jt.fullkey, 1, instr(jt.fullkey, '.content') - 1) || '.role'
                                        AND role_node.value = 'user'
                                    )
                                )
                            )
                        )
                        OR (
                            EXISTS (
                                SELECT 1
                                FROM json_tree(ce_search.payload, '$.final_response') jt
                                WHERE jt.type = 'text'
                                AND jt.value LIKE {response_content_placeholder} ESCAPE '\\'
                                AND (
                                    jt.fullkey = '$.final_response.content'
                                    OR jt.fullkey GLOB '$.final_response.content[*].text'
                                )
                            )
                        )
                    )
                )
                """.strip()
            )

    if search.from_time is not None:
        qualifying_having.append(
            f"MAX(ce.created_at) >= {add_param(search.from_time if db_pool.is_postgres else search.from_time.isoformat())}"
        )

    if search.to_time is not None:
        qualifying_having.append(
            f"MAX(ce.created_at) <= {add_param(search.to_time if db_pool.is_postgres else search.to_time.isoformat())}"
        )

    qualifying_where_sql = " AND ".join(qualifying_where)
    qualifying_having_sql = f"HAVING {' AND '.join(qualifying_having)}" if qualifying_having else ""
    policy_having_sql = f"HAVING {policy_count_expr} > 0" if search.policy_intervention else ""

    common_ctes = f"""
        WITH qualifying_sessions AS (
            SELECT ce.session_id
            FROM conversation_events ce
            WHERE {qualifying_where_sql}
            GROUP BY ce.session_id
            {qualifying_having_sql}
        ),
        session_stats AS (
            SELECT
                ce.session_id,
                MIN(ce.created_at) as first_ts,
                MAX(ce.created_at) as last_ts,
                COUNT(*) as total_events,
                COUNT(DISTINCT ce.call_id) as turn_count,
                {policy_count_expr} as policy_interventions
            FROM conversation_events ce
            INNER JOIN qualifying_sessions qs ON qs.session_id = ce.session_id
            GROUP BY ce.session_id
            {policy_having_sql}
        )
    """

    count_params = list(params)
    limit_placeholder = add_param(limit)
    offset_placeholder = add_param(offset)
    row_params = list(params)

    async with db_pool.connection() as conn:
        total_count = await conn.fetchval(
            f"""
            {common_ctes}
            SELECT COUNT(*) FROM session_stats
            """,
            *count_params,
        )

        rows = await conn.fetch(
            f"""
            {common_ctes}
            SELECT
                session_id,
                first_ts,
                last_ts,
                total_events,
                turn_count,
                policy_interventions
            FROM session_stats
            ORDER BY last_ts DESC
            LIMIT {limit_placeholder} OFFSET {offset_placeholder}
            """,
            *row_params,
        )

        total = int(total_count) if total_count is not None else 0  # type: ignore[arg-type]
        if not rows:
            return SessionListResponse(sessions=[], total=total, offset=offset, has_more=False)

        session_ids = [str(row["session_id"]) for row in rows]
        session_id_placeholders = ", ".join(f"${i + 1}" for i in range(len(session_ids)))

        model_rows = await conn.fetch(
            f"""
            SELECT session_id, {model_expr} as model
            FROM conversation_events
            WHERE session_id IN ({session_id_placeholders})
            AND event_type = 'transaction.request_recorded'
            AND {model_expr} IS NOT NULL
            """,
            *session_ids,
        )

        preview_rows = await conn.fetch(
            f"""
            SELECT session_id, payload as request_payload
            FROM conversation_events
            WHERE session_id IN ({session_id_placeholders})
            AND event_type = 'transaction.request_recorded'
            AND COALESCE(CAST({max_tokens_expr} AS INTEGER), 2) > 1
            ORDER BY session_id, created_at ASC
            """,
            *session_ids,
        )

    models_by_session: dict[str, list[str]] = {}
    for model_row in model_rows:
        sid = str(model_row["session_id"])
        model = str(model_row["model"])
        session_models = models_by_session.setdefault(sid, [])
        if model not in session_models:
            session_models.append(model)

    preview_by_session: dict[str, str | None] = {}
    for preview_row in preview_rows:
        sid = str(preview_row["session_id"])
        if sid not in preview_by_session:
            preview_by_session[sid] = _extract_preview_message(cast(_PreviewPayload, preview_row["request_payload"]))

    sessions = [
        SessionSummary(
            session_id=str(row["session_id"]),
            first_timestamp=parse_db_ts(row["first_ts"]).isoformat(),
            last_timestamp=parse_db_ts(row["last_ts"]).isoformat(),
            turn_count=int(row["turn_count"]),  # type: ignore[arg-type]
            total_events=int(row["total_events"]),  # type: ignore[arg-type]
            policy_interventions=int(row["policy_interventions"]),  # type: ignore[arg-type]
            models_used=models_by_session.get(str(row["session_id"]), []),
            preview_message=preview_by_session.get(str(row["session_id"])),
        )
        for row in rows
    ]

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
    calls: dict[str, list[StoredEvent]] = {}
    for row in rows:
        call_id = str(row["call_id"])
        if call_id not in calls:
            calls[call_id] = []

        raw_payload = row["payload"]
        if isinstance(raw_payload, dict):
            payload: dict[str, object] = dict(raw_payload)
        elif isinstance(raw_payload, str):
            payload = json.loads(raw_payload)
        else:
            raise TypeError(f"Unexpected payload type: {type(raw_payload).__name__}")

        raw_created_at = parse_db_ts(row["created_at"])

        calls[call_id].append(
            StoredEvent(
                event_type=str(row["event_type"]),
                payload=payload,
                created_at=raw_created_at,
            )
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

    first_ts_str = parse_db_ts(rows[0]["created_at"]).isoformat()
    last_ts_str = parse_db_ts(rows[-1]["created_at"]).isoformat()

    return SessionDetail(
        session_id=session_id,
        first_timestamp=first_ts_str,
        last_timestamp=last_ts_str,
        turns=turns,
        total_policy_interventions=total_interventions,
        models_used=sorted(all_models),
    )


_REQUEST_PARAM_ALLOWLIST = frozenset(
    {
        "model",
        "max_tokens",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "output_config",
    }
)


def _build_turn(call_id: str, events: list[StoredEvent]) -> ConversationTurn:
    """Build a conversation turn from a list of events for a call."""
    request_messages: list[ConversationMessage] = []
    response_messages: list[ConversationMessage] = []
    original_request_messages: list[ConversationMessage] | None = None
    original_response_messages: list[ConversationMessage] | None = None
    annotations: list[PolicyAnnotation] = []
    model: str | None = None
    timestamp: str = ""
    request_was_modified = False
    response_was_modified = False
    request_params: dict[str, Any] | None = None

    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        created_at = event["created_at"]

        if not timestamp:
            timestamp = created_at.isoformat()

        if event_type == "transaction.request_recorded":
            model = payload.get("final_model")
            original_req = payload.get("original_request")
            final_req = payload.get("final_request")

            if final_req is None:
                raise ValueError("transaction.request_recorded missing 'final_request'")

            request_messages = _parse_request_messages(final_req)

            # Pass through a curated set of request params so the
            # frontend can classify turn type (preflight, etc.).
            # Allowlist to avoid leaking sensitive/unknown fields.
            # tools_count is a synthetic field added below (not in the allowlist).
            request_params = {k: v for k, v in final_req.items() if k in _REQUEST_PARAM_ALLOWLIST}
            # Sanitize output_config: only pass format.type, not the full
            # schema body (which may contain proprietary structure info)
            oc = request_params.get("output_config")
            if isinstance(oc, dict):
                fmt = oc.get("format")
                if isinstance(fmt, dict):
                    request_params["output_config"] = {"format": {"type": fmt.get("type")}}
                else:
                    del request_params["output_config"]
            # Include tool count (not full definitions) for context
            tools = final_req.get("tools")
            if isinstance(tools, list):
                request_params["tools_count"] = len(tools)

            # Check for modifications at turn level
            if original_req is not None and original_req != final_req:
                request_was_modified = True
                original_request_messages = _parse_request_messages(original_req)

        elif event_type in (
            "transaction.streaming_response_recorded",
            "transaction.non_streaming_response_recorded",
        ):
            final_resp = payload.get("final_response")
            if final_resp is None:
                raise KeyError(f"{event_type} missing 'final_response'")

            response_messages = _parse_response_messages(final_resp)

            # Check for modifications at turn level
            original_resp = payload.get("original_response")
            if original_resp is not None and original_resp != final_resp:
                response_was_modified = True
                original_response_messages = _parse_response_messages(original_resp)

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

    had_intervention = request_was_modified or response_was_modified or bool(annotations)

    return ConversationTurn(
        call_id=call_id,
        timestamp=timestamp,
        model=model,
        request_messages=request_messages,
        response_messages=response_messages,
        annotations=annotations,
        had_policy_intervention=had_intervention,
        request_was_modified=request_was_modified,
        response_was_modified=response_was_modified,
        original_request_messages=original_request_messages,
        original_response_messages=original_response_messages,
        request_params=request_params,
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


def export_session_jsonl(session: SessionDetail) -> str:
    """Export a session as JSONL (one JSON object per turn).

    Each line contains a turn with call_id, session_id, model,
    request/response messages, and annotations.
    """
    lines: list[str] = []
    for turn in session.turns:
        record: dict[str, object] = {
            "call_id": turn.call_id,
            "session_id": session.session_id,
            "timestamp": turn.timestamp,
            "model": turn.model,
            "request_messages": [m.model_dump(mode="json") for m in turn.request_messages],
            "response_messages": [m.model_dump(mode="json") for m in turn.response_messages],
            "annotations": [a.model_dump(mode="json") for a in turn.annotations],
            "had_policy_intervention": turn.had_policy_intervention,
            "request_was_modified": turn.request_was_modified,
            "response_was_modified": turn.response_was_modified,
        }
        if turn.original_request_messages is not None:
            record["original_request_messages"] = [m.model_dump(mode="json") for m in turn.original_request_messages]
        if turn.original_response_messages is not None:
            record["original_response_messages"] = [m.model_dump(mode="json") for m in turn.original_response_messages]
        lines.append(json.dumps(record, default=str))
    return "\n".join(lines) + "\n" if lines else ""


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

    return "\n".join(lines)


__all__ = [
    "extract_text_content",
    "fetch_session_list",
    "fetch_session_detail",
    "export_session_markdown",
    "export_session_jsonl",
]
