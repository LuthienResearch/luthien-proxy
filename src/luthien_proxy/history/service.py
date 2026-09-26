"""Service layer for conversation history functionality.

Provides pure business logic for:
- Fetching session lists with summaries
- Fetching full session details with conversation turns
- Exporting sessions to markdown format
"""

from __future__ import annotations

import json
import logging
import weakref
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict, cast

from luthien_proxy.observability.session_summary import extract_preview
from luthien_proxy.utils.db import DatabasePool, parse_db_ts
from luthien_proxy.utils.search import session_fts_filter_sql

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


@dataclass(frozen=True, slots=True)
class CallEventRange:
    """Chronological call id and timestamp bounds for payload-scoped streaming."""

    call_id: str
    first_ts: datetime
    last_ts: datetime


@dataclass(frozen=True, slots=True)
class RequestDeltaState:
    """Running message-count state for server-side transcript delta emission."""

    prev_real_msg_count: int = 0


@dataclass(frozen=True, slots=True)
class RequestProjection:
    call_id: str
    first_ts: datetime
    request_ts: datetime
    final_model: str | None
    request_params: dict[str, Any]
    raw_msg_count: int
    request_was_modified: bool


@dataclass(frozen=True, slots=True)
class FastPathPlan:
    anchor: RequestProjection
    preflights: list[RequestProjection]


@dataclass(frozen=True, slots=True)
class SessionTurnWindow:
    ranges: list[CallEventRange]
    total_turns: int
    offset: int
    limit: int
    first_timestamp: datetime
    last_timestamp: datetime
    initial_prev_real_msg_count: int


@dataclass(frozen=True, slots=True)
class SessionDetailStats:
    total_policy_interventions: int
    models_used: list[str]


logger = logging.getLogger(__name__)
_SUMMARY_BACKFILLED_POOLS: weakref.WeakSet[DatabasePool] = weakref.WeakSet()
_SUMMARY_PREVIEWS_BACKFILLED_POOLS: weakref.WeakSet[DatabasePool] = weakref.WeakSet()
_NO_PREVIEW_SENTINEL = ""
_SESSION_DETAIL_BATCH_SIZE = 25
_SESSION_DETAIL_DEFAULT_LIMIT = 50
_SESSION_DETAIL_MAX_LIMIT = 200

# User-friendly descriptions for common policy event types.
# Note: every current emitter writes a non-empty `summary` into the event
# payload, and `_get_event_summary` returns that summary before consulting
# this dict. So this is fallback for events stored without a summary.
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
    # String replacement policy events
    "policy.string_replacement.request_modified": "Request modified by string replacement",
    "policy.string_replacement.response_modified": "Response modified by string replacement",
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
        messages.extend(_parse_raw_request_message(msg))

    return messages


def _parse_raw_request_message(msg: dict[str, Any]) -> list[ConversationMessage]:
    role = msg.get("role", "")
    msg_type = _ROLE_TO_MESSAGE_TYPE.get(role, MessageType.UNKNOWN)
    if msg_type == MessageType.UNKNOWN:
        raise ValueError(f"Unrecognized message role: '{role}'")

    content = extract_text_content(msg.get("content"))
    tool_call_id = msg.get("tool_call_id") if msg_type == MessageType.TOOL_RESULT else None
    if msg_type == MessageType.ASSISTANT:
        tool_call_msgs = _extract_tool_calls(msg)
        if tool_call_msgs:
            if content:
                return [*tool_call_msgs, ConversationMessage(message_type=msg_type, content=content)]
            return tool_call_msgs

    if msg_type == MessageType.USER:
        raw_content = msg.get("content")
        if isinstance(raw_content, list):
            has_tool_results = any(b.get("type") == "tool_result" for b in raw_content)
            if has_tool_results:
                parsed: list[ConversationMessage] = []
                for block in raw_content:
                    if block.get("type") == "tool_result":
                        result_content = block.get("content")
                        text = extract_text_content(result_content) if result_content is not None else ""
                        is_error = block.get("is_error") or None
                        parsed.append(
                            ConversationMessage(
                                message_type=MessageType.TOOL_RESULT,
                                content=text,
                                tool_call_id=block.get("tool_use_id"),
                                is_error=is_error,
                            )
                        )
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        parsed.append(ConversationMessage(message_type=MessageType.USER, content=block["text"]))
                return parsed

    return [ConversationMessage(message_type=msg_type, content=content, tool_call_id=tool_call_id)]


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


def _extract_preview_message(payload: dict[str, Any] | str | None) -> str | None:
    """Extract the first meaningful user message from a request payload for preview.

    Used to generate a session preview/title. Returns truncated text.
    Reads from ``original_request`` so the preview reflects what the user typed,
    not gateway-injected content (e.g. ``<policy-context>`` from
    ``inject_policy_awareness_anthropic``). Falls back to ``final_request`` for
    older payloads recorded before ``original_request`` was stored.
    """
    return extract_preview(payload)


# A "real" policy intervention is any policy.* event that is not a judge
# lifecycle/evaluation event. This predicate (over alias ``ce``) is the single
# source of truth — consumed by both the per-session stat aggregate and the
# policy_intervention filter, on both backends. Keep it here so the stat and
# the filter can never drift out of sync.
_INTERVENTION_PREDICATE = "ce.event_type LIKE 'policy.%' AND ce.event_type NOT LIKE 'policy.%judge.evaluation%'"


def _intervention_count_expr(is_postgres: bool) -> str:
    """Aggregate expression counting real policy interventions for a session.

    Postgres uses a ``COUNT(*) FILTER`` aggregate; SQLite lacks ``FILTER`` so it
    uses ``SUM(CASE ...)``. Both wrap the same :data:`_INTERVENTION_PREDICATE`.
    """
    if is_postgres:
        return f"COUNT(*) FILTER (WHERE {_INTERVENTION_PREDICATE})"
    return f"SUM(CASE WHEN {_INTERVENTION_PREDICATE} THEN 1 ELSE 0 END)"


def _build_session_filter_sql(
    search: SessionSearchParams,
    db_pool: DatabasePool,
    args: list[Any],
    *,
    user_scope_sql: str = "",
) -> tuple[list[str], list[str]]:
    """Build session-qualifying WHERE gates and HAVING clauses for a search.

    Appends bound parameters to ``args`` (asyncpg-style ``$N``, 1-indexed over
    the final args list) and returns ``(where_gates, having)``:

    * ``where_gates`` are *session-level* predicates over ``conversation_events
      ce`` — each of the form ``ce.session_id IN (...)``. Because they constrain
      ``session_id`` rather than individual events, a qualifying session keeps
      *all* of its events in the aggregation, so per-session stats (turn_count,
      models, policy_interventions) stay correct.
    * ``having`` are aggregate predicates ANDed into ``GROUP BY ... HAVING``.

    ``user_scope_sql`` (when non-empty) is an ``AND ce.call_id IN (...)`` clause
    using an already-allocated user_id placeholder; it is appended inside the
    model/q gate subqueries so a session can only qualify on *this* user's
    events. The time/policy HAVING clauses need no scoping — they run over the
    caller's session_stats, which is already user-scoped.

    SECURITY INVARIANT: every user-supplied value is bound via ``args`` and
    referenced only by a ``$N`` placeholder the builder controls; no user input
    is interpolated into SQL text. ``session_fts_filter_sql`` owns sanitizing
    the free-text ``q`` for each backend.
    """
    where_gates: list[str] = []
    having: list[str] = []

    def add_param(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if search.model is not None:
        model_col = "ce.payload->>'final_model'" if db_pool.is_postgres else "json_extract(ce.payload, '$.final_model')"
        placeholder = add_param(search.model)
        where_gates.append(
            "ce.session_id IN ("
            "SELECT ce.session_id FROM conversation_events ce "
            f"WHERE ce.event_type = 'transaction.request_recorded' AND {model_col} = {placeholder} "
            f"{user_scope_sql})"
        )

    if search.q and search.q.strip():
        # session_fts_filter_sql inlines the placeholder verbatim, so reserve the
        # next slot first, then bind the (sanitized) value it returns into it.
        placeholder = f"${len(args) + 1}"
        fragment, bind_value = session_fts_filter_sql(db_pool, search.q, placeholder=placeholder)
        add_param(bind_value)
        where_gates.append(
            f"ce.session_id IN (SELECT ce.session_id FROM conversation_events ce WHERE {fragment} {user_scope_sql})"
        )

    if search.from_time is not None:
        placeholder = add_param(search.from_time if db_pool.is_postgres else search.from_time.isoformat())
        having.append(f"MAX(ce.created_at) >= {placeholder}")

    if search.to_time is not None:
        placeholder = add_param(search.to_time if db_pool.is_postgres else search.to_time.isoformat())
        having.append(f"MAX(ce.created_at) <= {placeholder}")

    if search.policy_intervention:
        having.append(f"{_intervention_count_expr(db_pool.is_postgres)} > 0")

    return where_gates, having


def _gate_clause(where_gates: list[str]) -> str:
    """Render session-qualifying gates as a WHERE continuation (``AND ...``) or ``""``."""
    return ("AND " + " AND ".join(where_gates)) if where_gates else ""


def _having_clause(having: list[str]) -> str:
    """Render aggregate predicates as a ``HAVING ...`` clause or ``""``."""
    return ("HAVING " + " AND ".join(having)) if having else ""


def _models_from_summary(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return sorted(model for model in value.split(",") if model)
    if isinstance(value, list | tuple):
        return sorted(str(model) for model in value if model)
    return []


def _preview_from_summary(value: object) -> str | None:
    if value is None or value == _NO_PREVIEW_SENTINEL:
        return None
    return str(value)


def _last_timestamp_from_ranges(ranges: Sequence[CallEventRange]) -> datetime:
    return max(call_range.last_ts for call_range in ranges)


def _row_value(row: Any, primary: str, fallback: str) -> object:
    try:
        return row[primary]
    except KeyError:
        return row[fallback]


def _int_value(value: object) -> int:
    if isinstance(value, int | str | float):
        return int(value)
    raise TypeError(f"Expected numeric row value, got {type(value).__name__}")


def _clamp_detail_limit(limit: int) -> int:
    return min(max(limit, 1), _SESSION_DETAIL_MAX_LIMIT)


def _effective_detail_offset(offset: int | None, total_turns: int, limit: int) -> int:
    if offset is not None:
        return offset
    return max(0, total_turns - limit)


async def _fetch_session_turn_window(
    session_id: str,
    db_pool: DatabasePool,
    *,
    offset: int | None,
    limit: int,
) -> SessionTurnWindow:
    bounded_limit = _clamp_detail_limit(limit)
    is_sqlite = db_pool.is_sqlite is True
    async with db_pool.connection() as conn:
        summary_row = await conn.fetchrow(
            """
            SELECT MIN(created_at) AS first_ts,
                   MAX(created_at) AS last_ts,
                   COUNT(DISTINCT CASE WHEN event_type = 'transaction.request_recorded' THEN call_id END) AS total_turns
            FROM conversation_events
            WHERE session_id = $1
            """,
            session_id,
        )
        if summary_row is None or summary_row["first_ts"] is None:
            raise ValueError(f"No events found for session_id: {session_id}")
        total_turns = _int_value(summary_row["total_turns"])
        effective_offset = _effective_detail_offset(offset, total_turns, bounded_limit)
        if total_turns == 0 or effective_offset >= total_turns:
            return SessionTurnWindow(
                ranges=[],
                total_turns=total_turns,
                offset=effective_offset,
                limit=bounded_limit,
                first_timestamp=parse_db_ts(summary_row["first_ts"]),
                last_timestamp=parse_db_ts(summary_row["last_ts"]),
                initial_prev_real_msg_count=0,
            )
        request_rows = await conn.fetch(
            """
            SELECT call_id, created_at AS request_ts
            FROM conversation_events
            WHERE session_id = $1 AND event_type = 'transaction.request_recorded'
            ORDER BY created_at ASC
            LIMIT $2 OFFSET $3
            """,
            session_id,
            bounded_limit,
            effective_offset,
        )
        call_ids = [str(row["call_id"]) for row in request_rows]
        if not call_ids:
            return SessionTurnWindow(
                ranges=[],
                total_turns=total_turns,
                offset=effective_offset,
                limit=bounded_limit,
                first_timestamp=parse_db_ts(summary_row["first_ts"]),
                last_timestamp=parse_db_ts(summary_row["last_ts"]),
                initial_prev_real_msg_count=0,
            )
        first_request_ts = parse_db_ts(request_rows[0]["request_ts"])
        placeholders = ", ".join(f"${index}" for index in range(2, len(call_ids) + 2))
        if is_sqlite:
            range_rows = await conn.fetch(
                f"""
                SELECT call_id, MIN(created_at) AS first_ts, MAX(created_at) AS last_ts
                FROM conversation_events
                WHERE session_id = $1 AND call_id IN ({placeholders})
                GROUP BY call_id
                """,
                session_id,
                *call_ids,
            )
        else:
            range_rows = await conn.fetch(
                """
                SELECT call_id, MIN(created_at) AS first_ts, MAX(created_at) AS last_ts
                FROM conversation_events
                WHERE session_id = $1 AND call_id = ANY($2)
                GROUP BY call_id
                """,
                session_id,
                call_ids,
            )
        ranges_by_call_id = {
            str(row["call_id"]): CallEventRange(
                call_id=str(row["call_id"]),
                first_ts=parse_db_ts(row["first_ts"]),
                last_ts=parse_db_ts(row["last_ts"]),
            )
            for row in range_rows
        }
        previous_count = await _fetch_previous_real_raw_msg_count(
            conn,
            session_id,
            first_request_ts,
            is_sqlite=is_sqlite,
        )
        return SessionTurnWindow(
            ranges=[ranges_by_call_id[call_id] for call_id in call_ids if call_id in ranges_by_call_id],
            total_turns=total_turns,
            offset=effective_offset,
            limit=bounded_limit,
            first_timestamp=parse_db_ts(summary_row["first_ts"]),
            last_timestamp=parse_db_ts(summary_row["last_ts"]),
            initial_prev_real_msg_count=previous_count,
        )


async def _fetch_session_detail_stats(session_id: str, db_pool: DatabasePool) -> SessionDetailStats:
    is_sqlite = db_pool.is_sqlite is True
    async with db_pool.connection() as conn:
        summary_row = await conn.fetchrow(
            """
            SELECT policy_event_count, models_used
            FROM session_summaries
            WHERE session_id = $1
            """,
            session_id,
        )
        if summary_row is not None and summary_row["models_used"] is not None:
            return SessionDetailStats(
                total_policy_interventions=_int_value(summary_row["policy_event_count"]),
                models_used=_models_from_summary(summary_row["models_used"]),
            )
        if is_sqlite:
            stats_row = await conn.fetchrow(
                f"""
                SELECT
                    {_intervention_count_expr(False)} AS policy_interventions,
                    GROUP_CONCAT(DISTINCT json_extract(ce.payload, '$.final_model')) AS models_used
                FROM conversation_events ce
                WHERE ce.session_id = $1
                  AND (
                      ce.event_type <> 'transaction.request_recorded'
                      OR json_extract(ce.payload, '$.final_model') IS NOT NULL
                  )
                """,
                session_id,
            )
        else:
            stats_row = await conn.fetchrow(
                f"""
                SELECT
                    {_intervention_count_expr(True)} AS policy_interventions,
                    string_agg(DISTINCT ce.payload->>'final_model', ',') AS models_used
                FROM conversation_events ce
                WHERE ce.session_id = $1
                  AND (
                      ce.event_type <> 'transaction.request_recorded'
                      OR ce.payload->>'final_model' IS NOT NULL
                  )
                """,
                session_id,
            )
    if stats_row is None:
        return SessionDetailStats(total_policy_interventions=0, models_used=[])
    return SessionDetailStats(
        total_policy_interventions=_int_value(stats_row["policy_interventions"]),
        models_used=_models_from_summary(stats_row["models_used"]),
    )


async def _backfill_missing_session_summaries(db_pool: DatabasePool) -> None:
    if not isinstance(db_pool, DatabasePool):
        return
    if db_pool in _SUMMARY_BACKFILLED_POOLS:
        return
    async with db_pool.connection() as conn:
        if db_pool.is_postgres:
            await conn.execute(
                f"""
                INSERT INTO session_summaries (
                    session_id, first_seen, last_seen, event_count, call_count,
                    policy_event_count, user_id, models_used
                )
                SELECT
                    ce.session_id,
                    MIN(ce.created_at),
                    MAX(ce.created_at),
                    COUNT(*),
                    COUNT(*) FILTER (WHERE ce.event_type = 'transaction.request_recorded'),
                    {_intervention_count_expr(True)},
                    (SELECT cc.user_id FROM conversation_calls cc
                       WHERE cc.session_id = ce.session_id AND cc.user_id IS NOT NULL
                       ORDER BY cc.created_at LIMIT 1),
                    (SELECT string_agg(DISTINCT ce2.payload->>'final_model', ',')
                       FROM conversation_events ce2
                       WHERE ce2.session_id = ce.session_id
                         AND ce2.event_type = 'transaction.request_recorded'
                         AND ce2.payload->>'final_model' IS NOT NULL)
                FROM conversation_events ce
                WHERE ce.session_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM session_summaries ss WHERE ss.session_id = ce.session_id
                )
                GROUP BY ce.session_id
                ON CONFLICT (session_id) DO NOTHING
                """
            )
        else:
            await conn.execute(
                f"""
                INSERT OR IGNORE INTO session_summaries (
                    session_id, first_seen, last_seen, event_count, call_count,
                    policy_event_count, user_id, models_used
                )
                SELECT
                    ce.session_id,
                    MIN(ce.created_at),
                    MAX(ce.created_at),
                    COUNT(*),
                    SUM(CASE WHEN ce.event_type = 'transaction.request_recorded' THEN 1 ELSE 0 END),
                    {_intervention_count_expr(False)},
                    (SELECT cc.user_id FROM conversation_calls cc
                       WHERE cc.session_id = ce.session_id AND cc.user_id IS NOT NULL
                       ORDER BY cc.created_at LIMIT 1),
                    (SELECT GROUP_CONCAT(DISTINCT json_extract(ce2.payload, '$.final_model'))
                       FROM conversation_events ce2
                       WHERE ce2.session_id = ce.session_id
                         AND ce2.event_type = 'transaction.request_recorded'
                         AND json_extract(ce2.payload, '$.final_model') IS NOT NULL)
                FROM conversation_events ce
                WHERE ce.session_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM session_summaries ss WHERE ss.session_id = ce.session_id
                )
                GROUP BY ce.session_id
                """
            )
    _SUMMARY_BACKFILLED_POOLS.add(db_pool)


async def _backfill_session_summary_previews(db_pool: DatabasePool) -> None:
    if not isinstance(db_pool, DatabasePool):
        return
    if db_pool in _SUMMARY_PREVIEWS_BACKFILLED_POOLS:
        return
    batch_size = 500
    cursor = ""
    async with db_pool.connection() as conn:
        while True:
            session_rows = await conn.fetch(
                """
                SELECT session_id
                FROM session_summaries
                WHERE preview_message IS NULL AND session_id > $1
                ORDER BY session_id
                LIMIT $2
                """,
                cursor,
                batch_size,
            )
            if not session_rows:
                _SUMMARY_PREVIEWS_BACKFILLED_POOLS.add(db_pool)
                return
            for session_row in session_rows:
                session_id = str(session_row["session_id"])
                cursor = session_id
                if db_pool.is_postgres:
                    payload_row = await conn.fetchrow(
                        """
                        SELECT payload
                        FROM conversation_events
                        WHERE session_id = $1
                        AND event_type = 'transaction.request_recorded'
                        AND COALESCE((payload->'final_request'->>'max_tokens')::int, 2) > 1
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        session_id,
                    )
                else:
                    payload_row = await conn.fetchrow(
                        """
                        SELECT payload
                        FROM conversation_events
                        WHERE session_id = $1
                        AND event_type = 'transaction.request_recorded'
                        AND COALESCE(
                            CAST(json_extract(payload, '$.final_request.max_tokens') AS INTEGER),
                            2
                        ) > 1
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        session_id,
                    )
                preview: str | None = None
                if payload_row is not None:
                    raw_payload = payload_row["payload"]
                    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                    if isinstance(payload, dict):
                        preview = extract_preview(payload)
                await conn.execute(
                    "UPDATE session_summaries SET preview_message = $1 WHERE session_id = $2",
                    preview if preview is not None else _NO_PREVIEW_SENTINEL,
                    session_id,
                )


async def fetch_session_list(
    limit: int,
    db_pool: DatabasePool,
    offset: int = 0,
    *,
    user_id: str | None = None,
    search: SessionSearchParams | None = None,
) -> SessionListResponse:
    """Fetch list of recent sessions with summaries.

    Args:
        limit: Maximum number of sessions to return
        db_pool: Database connection pool
        offset: Number of sessions to skip for pagination
        user_id: If provided, only return sessions whose conversation_calls
            row has this exact user_id. Used to attribute traffic per user.
        search: Optional server-side filters (model, time range, full-text
            ``q``, policy_intervention). When None/empty the unfiltered hot path
            runs unchanged. ``total`` reflects the filtered count.

    Returns:
        List of session summaries ordered by most recent activity
    """
    search = search or SessionSearchParams()
    if db_pool.is_sqlite:
        return await _fetch_session_list_sqlite(limit, db_pool, offset, user_id=user_id, search=search)
    return await _fetch_session_list_pg(limit, db_pool, offset, user_id=user_id, search=search)


async def _fetch_session_list_pg(
    limit: int,
    db_pool: DatabasePool,
    offset: int = 0,
    *,
    user_id: str | None = None,
    search: SessionSearchParams | None = None,
) -> SessionListResponse:
    """PostgreSQL version using PG-specific features (FILTER, DISTINCT ON, array_agg)."""
    # SECURITY INVARIANT: user_id and every search value are bound as query
    # parameters, never interpolated into the SQL string. The user_id slot is
    # fixed at $3; search params (built by _build_session_filter_sql) occupy
    # $4+ when present. See test_fetch_session_list_user_filter_sql_injection.
    #
    # PERF: the user_id-population join to conversation_calls is intentionally
    # OUT of the main aggregation query. When no filter is requested we never
    # touch conversation_calls in the hot CTE — user_ids come from a separate
    # post-query keyed on the page's session_ids (mirrors the SQLite pattern).
    search = search or SessionSearchParams()
    if search.is_empty() and user_id is None:
        await _backfill_missing_session_summaries(db_pool)
        await _backfill_session_summary_previews(db_pool)
    async with db_pool.connection() as conn:
        if search.is_empty() and user_id is None:
            total_count = await conn.fetchval("SELECT COUNT(*) FROM session_summaries")
            rows = await conn.fetch(
                """
                SELECT
                    session_id,
                    first_seen as first_ts,
                    last_seen as last_ts,
                    event_count as total_events,
                    call_count as turn_count,
                    policy_event_count as policy_interventions,
                    models_used,
                    preview_message
                FROM session_summaries
                ORDER BY last_seen DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
            user_ids_by_session: dict[str, list[str]] = {}
            if rows:
                session_ids_on_page = [str(row["session_id"]) for row in rows]
                placeholders = ", ".join(f"${i + 1}" for i in range(len(session_ids_on_page)))
                user_id_rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT ce.session_id, cc.user_id
                    FROM conversation_events ce
                    JOIN conversation_calls cc ON ce.call_id = cc.call_id
                    WHERE ce.session_id IN ({placeholders})
                    AND cc.user_id IS NOT NULL
                    """,
                    *session_ids_on_page,
                )
                for r in user_id_rows:
                    sid = str(r["session_id"])
                    uid = str(r["user_id"])
                    bucket = user_ids_by_session.setdefault(sid, [])
                    if uid not in bucket:
                        bucket.append(uid)
            sessions = [
                SessionSummary(
                    session_id=str(row["session_id"]),
                    first_timestamp=parse_db_ts(row["first_ts"]).isoformat(),
                    last_timestamp=parse_db_ts(row["last_ts"]).isoformat(),
                    turn_count=_int_value(row["turn_count"]),
                    total_events=_int_value(row["total_events"]),
                    policy_interventions=_int_value(row["policy_interventions"]),
                    models_used=_models_from_summary(_row_value(row, "models_used", "models")),
                    preview_message=_preview_from_summary(row["preview_message"]),
                    user_ids=user_ids_by_session.get(str(row["session_id"]), []),
                )
                for row in rows
            ]
            total = _int_value(total_count) if total_count is not None else 0
            return SessionListResponse(
                sessions=sessions,
                total=total,
                offset=offset,
                has_more=offset + len(sessions) < total,
            )

        if search.is_empty():
            if user_id is not None:
                total_count = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT ce.session_id)
                    FROM conversation_events ce
                    JOIN conversation_calls cc ON ce.call_id = cc.call_id
                    WHERE ce.session_id IS NOT NULL AND cc.user_id = $1
                    """,
                    user_id,
                )
            else:
                total_count = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT session_id)
                    FROM conversation_events
                    WHERE session_id IS NOT NULL
                    """
                )
        else:
            # Filtered count: count sessions that survive the same qualifying
            # gates + HAVING as the page query. user_id (when set) is $1 here.
            count_args: list[Any] = []
            count_user_filter = ""
            if user_id is not None:
                count_args.append(user_id)
                count_user_filter = "AND ce.call_id IN (SELECT call_id FROM conversation_calls WHERE user_id = $1)"
            count_gates, count_having = _build_session_filter_sql(
                search, db_pool, count_args, user_scope_sql=count_user_filter
            )
            total_count = await conn.fetchval(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT ce.session_id
                    FROM conversation_events ce
                    WHERE ce.session_id IS NOT NULL
                    {count_user_filter}
                    {_gate_clause(count_gates)}
                    GROUP BY ce.session_id
                    {_having_clause(count_having)}
                ) AS qualifying
                """,
                *count_args,
            )

        # When the caller filters by user_id we restrict the events under
        # consideration to call_ids belonging to that user — a single shared
        # subquery used by every CTE so preview_message / models_used cannot
        # leak content from another user's calls under a shared session_id.
        user_call_filter = (
            "AND ce.call_id IN (SELECT call_id FROM conversation_calls WHERE user_id = $3)"
            if user_id is not None
            else ""
        )
        query_args: list[Any] = [limit, offset]
        if user_id is not None:
            query_args.append(user_id)

        # Search params occupy $4+ (after limit=$1, offset=$2, user_id=$3).
        where_gates, having = _build_session_filter_sql(search, db_pool, query_args, user_scope_sql=user_call_filter)
        gate_clause = _gate_clause(where_gates)
        having_clause = _having_clause(having)

        rows = await conn.fetch(
            f"""
            WITH session_stats AS (
                SELECT
                    ce.session_id,
                    MIN(ce.created_at) as first_ts,
                    MAX(ce.created_at) as last_ts,
                    COUNT(*) as total_events,
                    COUNT(DISTINCT ce.call_id) as turn_count,
                    {_intervention_count_expr(True)} as policy_interventions
                FROM conversation_events ce
                WHERE ce.session_id IS NOT NULL
                {user_call_filter}
                {gate_clause}
                GROUP BY ce.session_id
                {having_clause}
            ),
            session_models AS (
                SELECT DISTINCT
                    ce.session_id,
                    ce.payload->>'final_model' as model
                FROM conversation_events ce
                WHERE ce.session_id IS NOT NULL
                AND ce.event_type = 'transaction.request_recorded'
                AND ce.payload->>'final_model' IS NOT NULL
                {user_call_filter}
            ),
            session_first_message AS (
                SELECT DISTINCT ON (ce.session_id)
                    ce.session_id,
                    ce.payload as request_payload
                FROM conversation_events ce
                WHERE ce.session_id IS NOT NULL
                AND ce.event_type = 'transaction.request_recorded'
                -- Skip probe requests: max_tokens=1 means internal probe (token counting, quota).
                -- COALESCE to 2 so requests without max_tokens are not skipped.
                AND COALESCE((ce.payload->'final_request'->>'max_tokens')::int, 2) > 1
                {user_call_filter}
                ORDER BY ce.session_id, ce.created_at ASC
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
            *query_args,
        )

        # Separate user_ids lookup keyed on the page's session_ids. Distinct
        # users only — never collapse via MIN/MAX. When a user filter is in
        # effect the same scoping is applied so the response doesn't leak the
        # *existence* of other users sharing the session.
        user_ids_by_session: dict[str, list[str]] = {}
        if rows:
            session_ids_on_page = [str(row["session_id"]) for row in rows]
            placeholders = ", ".join(f"${i + 1}" for i in range(len(session_ids_on_page)))
            if user_id is not None:
                user_id_filter_clause = f"AND cc.user_id = ${len(session_ids_on_page) + 1}"
                user_id_extra_args: list[Any] = [user_id]
            else:
                user_id_filter_clause = ""
                user_id_extra_args = []
            user_id_rows = await conn.fetch(
                f"""
                SELECT DISTINCT ce.session_id, cc.user_id
                FROM conversation_events ce
                JOIN conversation_calls cc ON ce.call_id = cc.call_id
                WHERE ce.session_id IN ({placeholders})
                AND cc.user_id IS NOT NULL
                {user_id_filter_clause}
                """,
                *session_ids_on_page,
                *user_id_extra_args,
            )
            for r in user_id_rows:
                sid = str(r["session_id"])
                uid = str(r["user_id"])
                bucket = user_ids_by_session.setdefault(sid, [])
                if uid not in bucket:
                    bucket.append(uid)

    sessions = [
        SessionSummary(
            session_id=str(row["session_id"]),
            first_timestamp=parse_db_ts(row["first_ts"]).isoformat(),
            last_timestamp=parse_db_ts(row["last_ts"]).isoformat(),
            turn_count=int(row["turn_count"]),  # type: ignore[arg-type]
            total_events=int(row["total_events"]),  # type: ignore[arg-type]
            policy_interventions=int(row["policy_interventions"]),  # type: ignore[arg-type]
            models_used=_models_from_summary(row["models"]),
            preview_message=_extract_preview_message(cast(_PreviewPayload, row["request_payload"])),
            user_ids=user_ids_by_session.get(str(row["session_id"]), []),
        )
        for row in rows
    ]

    total = int(total_count) if total_count is not None else 0  # type: ignore[arg-type]
    has_more = offset + len(sessions) < total

    return SessionListResponse(sessions=sessions, total=total, offset=offset, has_more=has_more)


async def _fetch_session_list_sqlite(
    limit: int,
    db_pool: DatabasePool,
    offset: int = 0,
    *,
    user_id: str | None = None,
    search: SessionSearchParams | None = None,
) -> SessionListResponse:
    """SQLite version: 3 queries total (vs PostgreSQL's 2).

    Avoids N+1 by batching models and previews for the whole page in one
    query each, then merging in Python. PostgreSQL uses array_agg/DISTINCT ON
    in a single CTE; SQLite lacks those, so we use IN (session_ids) instead.
    """
    # SECURITY INVARIANT: user_id and every search value are bound as query
    # parameters, never interpolated into the SQL string. user_id occupies $3
    # in the page query ($1 in the filtered count); search params follow.
    search = search or SessionSearchParams()
    if search.is_empty() and user_id is None:
        await _backfill_missing_session_summaries(db_pool)
        await _backfill_session_summary_previews(db_pool)
    async with db_pool.connection() as conn:
        if search.is_empty() and user_id is None:
            total_count = await conn.fetchval("SELECT COUNT(*) FROM session_summaries")
            rows = await conn.fetch(
                """
                SELECT
                    session_id,
                    first_seen as first_ts,
                    last_seen as last_ts,
                    event_count as total_events,
                    call_count as turn_count,
                    policy_event_count as policy_interventions,
                    models_used,
                    preview_message
                FROM session_summaries
                ORDER BY last_seen DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
            total = _int_value(total_count) if total_count is not None else 0
            if not rows:
                return SessionListResponse(sessions=[], total=total, offset=offset, has_more=False)

            session_ids = [str(row["session_id"]) for row in rows]
            placeholders = ", ".join(f"${i + 1}" for i in range(len(session_ids)))
            user_id_rows = await conn.fetch(
                f"""
                SELECT DISTINCT ce.session_id, cc.user_id
                FROM conversation_events ce
                JOIN conversation_calls cc ON ce.call_id = cc.call_id
                WHERE ce.session_id IN ({placeholders})
                AND cc.user_id IS NOT NULL
                """,
                *session_ids,
            )
            user_ids_by_session: dict[str, list[str]] = {}
            for r in user_id_rows:
                sid = str(r["session_id"])
                uid = str(r["user_id"])
                bucket = user_ids_by_session.setdefault(sid, [])
                if uid not in bucket:
                    bucket.append(uid)
            sessions = [
                SessionSummary(
                    session_id=str(row["session_id"]),
                    first_timestamp=parse_db_ts(row["first_ts"]).isoformat(),
                    last_timestamp=parse_db_ts(row["last_ts"]).isoformat(),
                    turn_count=_int_value(row["turn_count"]),
                    total_events=_int_value(row["total_events"]),
                    policy_interventions=_int_value(row["policy_interventions"]),
                    models_used=_models_from_summary(_row_value(row, "models_used", "models")),
                    preview_message=_preview_from_summary(row["preview_message"]),
                    user_ids=user_ids_by_session.get(str(row["session_id"]), []),
                )
                for row in rows
            ]
            return SessionListResponse(
                sessions=sessions,
                total=total,
                offset=offset,
                has_more=offset + len(sessions) < total,
            )

        if search.is_empty():
            if user_id is not None:
                total_count = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT ce.session_id)
                    FROM conversation_events ce
                    JOIN conversation_calls cc ON ce.call_id = cc.call_id
                    WHERE ce.session_id IS NOT NULL AND cc.user_id = $1
                    """,
                    user_id,
                )
            else:
                total_count = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT session_id)
                    FROM conversation_events
                    WHERE session_id IS NOT NULL
                    """
                )
        else:
            count_args: list[Any] = []
            count_user_filter = ""
            if user_id is not None:
                count_args.append(user_id)
                count_user_filter = "AND ce.call_id IN (SELECT call_id FROM conversation_calls WHERE user_id = $1)"
            count_gates, count_having = _build_session_filter_sql(
                search, db_pool, count_args, user_scope_sql=count_user_filter
            )
            total_count = await conn.fetchval(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT ce.session_id
                    FROM conversation_events ce
                    WHERE ce.session_id IS NOT NULL
                    {count_user_filter}
                    {_gate_clause(count_gates)}
                    GROUP BY ce.session_id
                    {_having_clause(count_having)}
                ) AS qualifying
                """,
                *count_args,
            )

        # PERF: only filter through conversation_calls when a user filter is
        # actually requested. Unfiltered list calls (the hot path) skip the
        # conversation_calls subquery entirely. user_ids are populated by a
        # separate post-query keyed on the page's session_ids (SQLite has no
        # array_agg, so we can't compute them inside this query anyway).
        user_call_filter = (
            "AND ce.call_id IN (SELECT call_id FROM conversation_calls WHERE user_id = $3)"
            if user_id is not None
            else ""
        )
        query_args: list[Any] = [limit, offset]
        if user_id is not None:
            query_args.append(user_id)

        # Search params occupy $4+ (after limit=$1, offset=$2, user_id=$3).
        where_gates, having = _build_session_filter_sql(search, db_pool, query_args, user_scope_sql=user_call_filter)

        rows = await conn.fetch(
            f"""
            SELECT
                ce.session_id,
                MIN(ce.created_at) as first_ts,
                MAX(ce.created_at) as last_ts,
                COUNT(*) as total_events,
                COUNT(DISTINCT ce.call_id) as turn_count,
                {_intervention_count_expr(False)} as policy_interventions
            FROM conversation_events ce
            WHERE ce.session_id IS NOT NULL
            {user_call_filter}
            {_gate_clause(where_gates)}
            GROUP BY ce.session_id
            {_having_clause(having)}
            ORDER BY last_ts DESC
            LIMIT $1 OFFSET $2
            """,
            *query_args,
        )

        total = int(total_count) if total_count is not None else 0  # type: ignore[arg-type]

        if not rows:
            return SessionListResponse(sessions=[], total=total, offset=offset, has_more=False)

        session_ids = [str(row["session_id"]) for row in rows]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(session_ids)))

        # When a user_id filter is in effect, restrict the model/preview/user-id
        # lookups to that user's call_ids — without this, preview_message and
        # models_used can leak content from other users' calls that happen to
        # share the session_id.
        # NOTE: this clause is *separate from* the `user_call_filter` used in
        # the main aggregation above — different placeholder slot ($N differs
        # because session_ids are also bound here). Don't fold into one.
        if user_id is not None:
            user_call_filter_lookups = (
                f"AND ce.call_id IN (SELECT call_id FROM conversation_calls WHERE user_id = ${len(session_ids) + 1})"
            )
            extra_args: list[Any] = [user_id]
        else:
            user_call_filter_lookups = ""
            extra_args = []

        # One query for all models on this page
        model_rows = await conn.fetch(
            f"""
            SELECT ce.session_id, json_extract(ce.payload, '$.final_model') as model
            FROM conversation_events ce
            WHERE ce.session_id IN ({placeholders})
            AND ce.event_type = 'transaction.request_recorded'
            AND json_extract(ce.payload, '$.final_model') IS NOT NULL
            {user_call_filter_lookups}
            """,
            *session_ids,
            *extra_args,
        )

        # One query for first qualifying preview per session on this page
        preview_rows = await conn.fetch(
            f"""
            SELECT ce.session_id, ce.payload as request_payload
            FROM conversation_events ce
            WHERE ce.session_id IN ({placeholders})
            AND ce.event_type = 'transaction.request_recorded'
            AND COALESCE(
                CAST(json_extract(ce.payload, '$.final_request.max_tokens') AS INTEGER),
                2
            ) > 1
            {user_call_filter_lookups}
            ORDER BY ce.session_id, ce.created_at ASC
            """,
            *session_ids,
            *extra_args,
        )

        # Distinct user_ids per session — never collapse via MIN/MAX, that lies
        # on multi-user sessions. Returned as a list so the consumer can render
        # mixed-identity sessions honestly. When a user filter is in effect
        # we constrain to that user so the response doesn't leak the *existence*
        # of other users sharing the session.
        if user_id is not None:
            user_id_filter_clause = f"AND cc.user_id = ${len(session_ids) + 1}"
            user_id_args: list[Any] = [user_id]
        else:
            user_id_filter_clause = ""
            user_id_args = []
        user_id_rows = await conn.fetch(
            f"""
            SELECT DISTINCT ce.session_id, cc.user_id
            FROM conversation_events ce
            JOIN conversation_calls cc ON ce.call_id = cc.call_id
            WHERE ce.session_id IN ({placeholders})
            AND cc.user_id IS NOT NULL
            {user_id_filter_clause}
            """,
            *session_ids,
            *user_id_args,
        )

    # Build per-session lookup maps from the bulk results
    models_by_session: dict[str, list[str]] = {}
    for r in model_rows:
        sid = str(r["session_id"])
        model = str(r["model"])
        session_models = models_by_session.setdefault(sid, [])
        if model not in session_models:
            session_models.append(model)

    preview_by_session: dict[str, str | None] = {}
    for r in preview_rows:
        sid = str(r["session_id"])
        if sid not in preview_by_session:
            preview_by_session[sid] = _extract_preview_message(cast(_PreviewPayload, r["request_payload"]))

    user_ids_by_session: dict[str, list[str]] = {}
    for r in user_id_rows:
        sid = str(r["session_id"])
        uid = str(r["user_id"])
        bucket = user_ids_by_session.setdefault(sid, [])
        if uid not in bucket:
            bucket.append(uid)

    sessions = [
        SessionSummary(
            session_id=str(row["session_id"]),
            first_timestamp=parse_db_ts(row["first_ts"]).isoformat(),
            last_timestamp=parse_db_ts(row["last_ts"]).isoformat(),
            turn_count=int(row["turn_count"]),  # type: ignore[arg-type]
            total_events=int(row["total_events"]),  # type: ignore[arg-type]
            policy_interventions=int(row["policy_interventions"]),  # type: ignore[arg-type]
            models_used=sorted(models_by_session.get(str(row["session_id"]), [])),
            preview_message=preview_by_session.get(str(row["session_id"])),
            user_ids=user_ids_by_session.get(str(row["session_id"]), []),
        )
        for row in rows
    ]

    has_more = offset + len(sessions) < total
    return SessionListResponse(sessions=sessions, total=total, offset=offset, has_more=has_more)


async def fetch_session_detail(
    session_id: str,
    db_pool: DatabasePool,
    *,
    offset: int | None = None,
    limit: int = _SESSION_DETAIL_DEFAULT_LIMIT,
) -> SessionDetail:
    """Fetch full session detail with conversation turns.

    Args:
        session_id: Session identifier
        db_pool: Database connection pool
        offset: Chronological turn offset. None selects the newest page.
        limit: Maximum number of turns to return, capped by the service.

    Returns:
        Full session detail with all conversation turns

    Raises:
        ValueError: If no events found for session_id
    """
    window = await _fetch_session_turn_window(session_id, db_pool, offset=offset, limit=limit)
    stats = await _fetch_session_detail_stats(session_id, db_pool)
    turns: list[ConversationTurn] = []
    async for turn in iter_session_turns(
        session_id,
        db_pool,
        window.ranges,
        initial_prev_real_msg_count=window.initial_prev_real_msg_count,
        projection_offset=window.offset,
        projection_limit=window.limit,
    ):
        turns.append(turn)

    return SessionDetail(
        session_id=session_id,
        first_timestamp=window.first_timestamp.isoformat(),
        last_timestamp=window.last_timestamp.isoformat(),
        turns=turns,
        total_policy_interventions=stats.total_policy_interventions,
        models_used=stats.models_used,
        total_turns=window.total_turns,
        offset=window.offset,
        limit=window.limit,
        has_more=window.offset > 0,
    )


def _stored_events_from_rows(rows: Sequence[Mapping[str, object]]) -> list[StoredEvent]:
    events: list[StoredEvent] = []
    for row in rows:
        raw_payload = row["payload"]
        if isinstance(raw_payload, dict):
            payload: dict[str, Any] = dict(raw_payload)
        elif isinstance(raw_payload, str):
            payload = json.loads(raw_payload)
        else:
            raise TypeError(f"Unexpected payload type: {type(raw_payload).__name__}")
        events.append(
            StoredEvent(
                event_type=str(row["event_type"]),
                payload=payload,
                created_at=parse_db_ts(row["created_at"]),
            )
        )
    return events


def _is_preflight_turn(turn: ConversationTurn) -> bool:
    params = turn.request_params or {}
    max_tokens = params.get("max_tokens")
    if max_tokens == 1:
        return True
    output_config = params.get("output_config")
    if not isinstance(output_config, dict):
        return False
    output_format = output_config.get("format")
    if not isinstance(output_format, dict):
        return False
    if output_format.get("type") != "json_schema":
        return False
    return isinstance(max_tokens, int) and max_tokens <= 256


def _is_preflight_projection(projection: RequestProjection) -> bool:
    max_tokens = projection.request_params.get("max_tokens")
    if max_tokens == 1:
        return True
    output_config = projection.request_params.get("output_config")
    if not isinstance(output_config, dict):
        return False
    output_format = output_config.get("format")
    if not isinstance(output_format, dict):
        return False
    if output_format.get("type") != "json_schema":
        return False
    return isinstance(max_tokens, int) and max_tokens <= 256


def _select_fast_path_anchor(projections: Sequence[RequestProjection]) -> FastPathPlan | None:
    real_projections: list[RequestProjection] = []
    preflights: list[RequestProjection] = []
    previous_count = 0
    for projection in projections:
        if projection.request_was_modified:
            return None
        if _is_preflight_projection(projection):
            preflights.append(projection)
            continue
        if projection.raw_msg_count < previous_count:
            return None
        previous_count = projection.raw_msg_count
        real_projections.append(projection)
    if not real_projections:
        return None
    anchor = max(real_projections, key=lambda projection: projection.raw_msg_count)
    return FastPathPlan(anchor=anchor, preflights=preflights)


def _parsed_prefixes_by_raw_count(raw_messages: Sequence[dict[str, Any]]) -> dict[int, list[ConversationMessage]]:
    parsed_messages: list[ConversationMessage] = []
    prefixes: dict[int, list[ConversationMessage]] = {0: []}
    for index, raw_message in enumerate(raw_messages, start=1):
        parsed_messages.extend(_parse_raw_request_message(raw_message))
        prefixes[index] = list(parsed_messages)
    return prefixes


def _turn_from_projection(
    projection: RequestProjection,
    events: list[StoredEvent],
    request_messages: list[ConversationMessage],
) -> ConversationTurn:
    response_messages: list[ConversationMessage] = []
    original_response_messages: list[ConversationMessage] | None = None
    annotations: list[PolicyAnnotation] = []
    response_was_modified = False
    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        if event_type in (
            "transaction.streaming_response_recorded",
            "transaction.non_streaming_response_recorded",
        ):
            final_resp = payload.get("final_response")
            if final_resp is None:
                raise KeyError(f"{event_type} missing 'final_response'")
            response_messages = _parse_response_messages(final_resp)
            original_resp = payload.get("original_response")
            if original_resp is not None and original_resp != final_resp:
                response_was_modified = True
                original_response_messages = _parse_response_messages(original_resp)
        elif event_type.startswith("policy."):
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
    return ConversationTurn(
        call_id=projection.call_id,
        timestamp=projection.first_ts.isoformat(),
        model=projection.final_model,
        request_messages=request_messages,
        response_messages=response_messages,
        annotations=annotations,
        had_policy_intervention=response_was_modified or bool(annotations),
        request_was_modified=False,
        response_was_modified=response_was_modified,
        original_response_messages=original_response_messages,
        request_params=projection.request_params,
    )


async def _iter_session_turns_fast(
    projections: Sequence[RequestProjection],
    events_by_call_id: Mapping[str, list[StoredEvent]],
    messages_by_call_id: Mapping[str, list[dict[str, Any]]],
    *,
    initial_prev_real_msg_count: int = 0,
) -> AsyncIterator[ConversationTurn]:
    plan = _select_fast_path_anchor(projections)
    if plan is None:
        return
    anchor_messages = messages_by_call_id[plan.anchor.call_id]
    prefixes = _parsed_prefixes_by_raw_count(anchor_messages)
    previous_real_count = initial_prev_real_msg_count
    for projection in projections:
        if _is_preflight_projection(projection):
            request_messages = _parse_request_messages({"messages": messages_by_call_id[projection.call_id]})
        else:
            current_prefix = prefixes[projection.raw_msg_count]
            previous_prefix = prefixes[previous_real_count]
            request_messages = current_prefix[len(previous_prefix) :]
            previous_real_count = projection.raw_msg_count
        yield _turn_from_projection(projection, events_by_call_id.get(projection.call_id, []), request_messages)


def _apply_request_delta(
    turn: ConversationTurn, state: RequestDeltaState
) -> tuple[ConversationTurn, RequestDeltaState]:
    full_request_messages = turn.request_messages
    request_messages_full = full_request_messages if turn.request_was_modified else None
    if _is_preflight_turn(turn):
        return (
            turn.model_copy(
                update={
                    "request_messages": full_request_messages,
                    "request_messages_full": request_messages_full,
                }
            ),
            state,
        )

    request_delta = full_request_messages[state.prev_real_msg_count :]
    return (
        turn.model_copy(
            update={
                "request_messages": request_delta,
                "request_messages_full": request_messages_full,
            }
        ),
        RequestDeltaState(prev_real_msg_count=len(full_request_messages)),
    )


def _range_batches(ranges: Sequence[CallEventRange]) -> list[Sequence[CallEventRange]]:
    return [
        ranges[index : index + _SESSION_DETAIL_BATCH_SIZE]
        for index in range(0, len(ranges), _SESSION_DETAIL_BATCH_SIZE)
    ]


async def _fetch_turn_batch_rows(
    conn: Any, session_id: str, batch: Sequence[CallEventRange], *, is_sqlite: bool
) -> dict[str, list[Mapping[str, object]]]:
    if is_sqlite:
        placeholders = ", ".join(f"${index}" for index in range(2, len(batch) + 2))
        rows = await conn.fetch(
            f"""
            SELECT call_id, event_type, payload, created_at
            FROM conversation_events
            WHERE session_id = $1 AND call_id IN ({placeholders})
            ORDER BY call_id, created_at ASC
            """,
            session_id,
            *(call_range.call_id for call_range in batch),
        )
    else:
        rows = await conn.fetch(
            """
            SELECT call_id, event_type, payload, created_at
            FROM conversation_events
            WHERE session_id = $1 AND call_id = ANY($2)
            ORDER BY call_id, created_at ASC
            """,
            session_id,
            [call_range.call_id for call_range in batch],
        )

    range_by_call_id = {call_range.call_id: call_range for call_range in batch}
    grouped: dict[str, list[Mapping[str, object]]] = {call_range.call_id: [] for call_range in batch}
    for row in rows:
        call_id = str(row["call_id"])
        call_range = range_by_call_id.get(call_id)
        if call_range is not None and parse_db_ts(row["created_at"]) <= call_range.last_ts:
            grouped[call_id].append(row)
    return grouped


async def _fetch_non_request_event_rows(
    conn: Any, session_id: str, ranges: Sequence[CallEventRange], *, is_sqlite: bool
) -> dict[str, list[Mapping[str, object]]]:
    if not ranges:
        return {}
    if is_sqlite:
        placeholders = ", ".join(f"${index}" for index in range(2, len(ranges) + 2))
        rows = await conn.fetch(
            f"""
            SELECT call_id, event_type, payload, created_at
            FROM conversation_events
            WHERE session_id = $1 AND call_id IN ({placeholders}) AND event_type <> 'transaction.request_recorded'
            ORDER BY call_id, created_at ASC
            """,
            session_id,
            *(call_range.call_id for call_range in ranges),
        )
    else:
        rows = await conn.fetch(
            """
            SELECT call_id, event_type, payload, created_at
            FROM conversation_events
            WHERE session_id = $1 AND call_id = ANY($2) AND event_type <> 'transaction.request_recorded'
            ORDER BY call_id, created_at ASC
            """,
            session_id,
            [call_range.call_id for call_range in ranges],
        )
    range_by_call_id = {call_range.call_id: call_range for call_range in ranges}
    grouped: dict[str, list[Mapping[str, object]]] = {call_range.call_id: [] for call_range in ranges}
    for row in rows:
        call_id = str(row["call_id"])
        call_range = range_by_call_id.get(call_id)
        if call_range is not None and parse_db_ts(row["created_at"]) <= call_range.last_ts:
            grouped[call_id].append(row)
    return grouped


def _json_obj(raw_value: object) -> dict[str, Any] | None:
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if isinstance(raw_value, str):
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else None
    return None


def _json_list(raw_value: object) -> list[dict[str, Any]]:
    parsed: object = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    if not isinstance(parsed, list):
        raise TypeError(f"Expected request messages list, got {type(parsed).__name__}")
    return [dict(item) for item in parsed if isinstance(item, dict)]


@dataclass(frozen=True, slots=True)
class _OmittedRequestParam:
    pass


_OMITTED_REQUEST_PARAM = _OmittedRequestParam()


def _request_param_is_present(row: Mapping[str, object], key: str) -> bool:
    present_value = row.get(f"{key}_present")
    if isinstance(present_value, bool):
        return present_value
    if isinstance(present_value, int):
        return bool(present_value)
    return row.get(key) is not None


def _raw_request_param(raw_value: Any) -> Any | _OmittedRequestParam:
    return raw_value


def _bool_request_param(raw_value: Any) -> Any | _OmittedRequestParam:
    if isinstance(raw_value, bool):
        return raw_value
    if type(raw_value) is int and raw_value in (0, 1):
        return bool(raw_value)
    return raw_value


def _json_list_request_param(raw_value: Any) -> Any | _OmittedRequestParam:
    if isinstance(raw_value, str):
        try:
            parsed_value = json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value
        return parsed_value if isinstance(parsed_value, list) else raw_value
    return raw_value


def _output_config_request_param(raw_value: Any) -> Any | _OmittedRequestParam:
    if raw_value is None:
        return None
    output_config = _json_obj(raw_value)
    if output_config is None:
        return _OMITTED_REQUEST_PARAM
    output_format = output_config.get("format")
    if not isinstance(output_format, dict):
        return _OMITTED_REQUEST_PARAM
    return {"format": {"type": output_format.get("type")}}


def _projection_params_from_row(row: Mapping[str, object]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in _REQUEST_PARAM_ALLOWLIST:
        if not _request_param_is_present(row, key):
            continue
        normalized_value = _REQUEST_PARAM_NORMALIZERS[key](row.get(key))
        if normalized_value is not _OMITTED_REQUEST_PARAM:
            params[key] = normalized_value
    tools_count = row.get("tools_count")
    if isinstance(tools_count, int):
        params["tools_count"] = tools_count
    return params


def _projection_from_row(row: Mapping[str, object]) -> RequestProjection:
    raw_msg_count = row["raw_msg_count"]
    if not isinstance(raw_msg_count, int):
        raise TypeError(f"Expected raw_msg_count int, got {type(raw_msg_count).__name__}")
    return RequestProjection(
        call_id=str(row["call_id"]),
        first_ts=parse_db_ts(row["first_ts"]),
        request_ts=parse_db_ts(row["request_ts"]),
        final_model=str(row["final_model"]) if row.get("final_model") is not None else None,
        request_params=_projection_params_from_row(row),
        raw_msg_count=raw_msg_count,
        request_was_modified=bool(row["request_was_modified"]),
    )


async def _fetch_previous_real_raw_msg_count(
    conn: Any, session_id: str, before_ts: datetime, *, is_sqlite: bool
) -> int:
    if is_sqlite:
        row = await conn.fetchrow(
            """
            SELECT json_array_length(json_extract(payload, '$.final_request.messages')) AS raw_msg_count
            FROM conversation_events
            WHERE session_id = $1
              AND event_type = 'transaction.request_recorded'
              AND created_at < $2
              AND NOT (
                  COALESCE(json_extract(payload, '$.final_request.max_tokens') = 1, 0)
                  OR COALESCE(
                      json_extract(payload, '$.final_request.output_config.format.type') = 'json_schema'
                      AND json_extract(payload, '$.final_request.max_tokens') <= 256,
                      0
                  )
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
            before_ts.isoformat(),
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT jsonb_array_length(payload->'final_request'->'messages') AS raw_msg_count
            FROM conversation_events
            WHERE session_id = $1
              AND event_type = 'transaction.request_recorded'
              AND created_at < $2
              AND NOT (
                  COALESCE((payload->'final_request'->>'max_tokens')::integer = 1, false)
                  OR COALESCE(
                      payload->'final_request'->'output_config'->'format'->>'type' = 'json_schema'
                      AND (payload->'final_request'->>'max_tokens')::integer <= 256,
                      false
                  )
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
            before_ts,
        )
    if row is None or row["raw_msg_count"] is None:
        return 0
    return _int_value(row["raw_msg_count"])


async def _fetch_request_projections(
    conn: Any,
    session_id: str,
    ranges: Sequence[CallEventRange],
    *,
    is_sqlite: bool,
    offset: int | None = None,
    limit: int | None = None,
) -> list[RequestProjection]:
    if not ranges:
        return []
    if is_sqlite:
        if offset is None or limit is None:
            where_clause = f"session_id = $1 AND call_id IN ({', '.join(f'${index}' for index in range(2, len(ranges) + 2))}) AND event_type = 'transaction.request_recorded'"
            query_args = (session_id, *(call_range.call_id for call_range in ranges))
            source_clause = f"conversation_events WHERE {where_clause}"
        else:
            query_args = (session_id, limit, offset)
            source_clause = """
                (
                    SELECT call_id, created_at, payload
                    FROM conversation_events
                    WHERE session_id = $1 AND event_type = 'transaction.request_recorded'
                    ORDER BY created_at ASC
                    LIMIT $2 OFFSET $3
                ) AS window_events
            """
        rows = await conn.fetch(
            f"""
            SELECT call_id,
                   MIN(created_at) OVER (PARTITION BY call_id) AS first_ts,
                   created_at AS request_ts,
                   json_extract(payload, '$.final_model') AS final_model,
                   json_extract(payload, '$.final_request.model') AS model,
                   json_extract(payload, '$.final_request.max_tokens') AS max_tokens,
                   json_extract(payload, '$.final_request.stream') AS stream,
                   json_extract(payload, '$.final_request.temperature') AS temperature,
                   json_extract(payload, '$.final_request.top_p') AS top_p,
                   json_extract(payload, '$.final_request.top_k') AS top_k,
                   json_extract(payload, '$.final_request.stop_sequences') AS stop_sequences,
                   json_extract(payload, '$.final_request.output_config') AS output_config,
                   json_type(payload, '$.final_request.model') IS NOT NULL AS model_present,
                   json_type(payload, '$.final_request.max_tokens') IS NOT NULL AS max_tokens_present,
                   json_type(payload, '$.final_request.stream') IS NOT NULL AS stream_present,
                   json_type(payload, '$.final_request.temperature') IS NOT NULL AS temperature_present,
                   json_type(payload, '$.final_request.top_p') IS NOT NULL AS top_p_present,
                   json_type(payload, '$.final_request.top_k') IS NOT NULL AS top_k_present,
                   json_type(payload, '$.final_request.stop_sequences') IS NOT NULL AS stop_sequences_present,
                   json_type(payload, '$.final_request.output_config') IS NOT NULL AS output_config_present,
                   json_array_length(json_extract(payload, '$.final_request.tools')) AS tools_count,
                   json_array_length(json_extract(payload, '$.final_request.messages')) AS raw_msg_count,
                   CASE
                       WHEN json_type(payload, '$.original_request') IS NULL THEN 0
                       WHEN json_type(payload, '$.final_request') IS NULL THEN 1
                       ELSE json_extract(payload, '$.original_request') <> json_extract(payload, '$.final_request')
                   END AS request_was_modified
            FROM {source_clause}
            ORDER BY call_id, created_at ASC
            """,
            *query_args,
        )
    else:
        if offset is None or limit is None:
            where_clause = "session_id = $1 AND call_id = ANY($2) AND event_type = 'transaction.request_recorded'"
            query_args = (session_id, [call_range.call_id for call_range in ranges])
            source_clause = f"conversation_events WHERE {where_clause}"
        else:
            query_args = (session_id, limit, offset)
            source_clause = """
                (
                    SELECT call_id, created_at, payload
                    FROM conversation_events
                    WHERE session_id = $1 AND event_type = 'transaction.request_recorded'
                    ORDER BY created_at ASC
                    LIMIT $2 OFFSET $3
                ) AS window_events
            """
        rows = await conn.fetch(
            f"""
            SELECT call_id,
                   MIN(created_at) OVER (PARTITION BY call_id) AS first_ts,
                   created_at AS request_ts,
                   payload->>'final_model' AS final_model,
                   payload->'final_request'->>'model' AS model,
                   (payload->'final_request'->>'max_tokens')::integer AS max_tokens,
                   (payload->'final_request'->>'stream')::boolean AS stream,
                   (payload->'final_request'->>'temperature')::double precision AS temperature,
                   (payload->'final_request'->>'top_p')::double precision AS top_p,
                   (payload->'final_request'->>'top_k')::integer AS top_k,
                   payload->'final_request'->'stop_sequences' AS stop_sequences,
                   payload->'final_request'->'output_config' AS output_config,
                   payload->'final_request' ? 'model' AS model_present,
                   payload->'final_request' ? 'max_tokens' AS max_tokens_present,
                   payload->'final_request' ? 'stream' AS stream_present,
                   payload->'final_request' ? 'temperature' AS temperature_present,
                   payload->'final_request' ? 'top_p' AS top_p_present,
                   payload->'final_request' ? 'top_k' AS top_k_present,
                   payload->'final_request' ? 'stop_sequences' AS stop_sequences_present,
                   payload->'final_request' ? 'output_config' AS output_config_present,
                   jsonb_array_length(COALESCE(payload->'final_request'->'tools', '[]'::jsonb)) AS tools_count,
                   jsonb_array_length(payload->'final_request'->'messages') AS raw_msg_count,
                   (payload->'original_request') IS NOT NULL
                       AND (payload->'original_request') <> (payload->'final_request') AS request_was_modified
            FROM {source_clause}
            ORDER BY call_id, created_at ASC
            """,
            *query_args,
        )

    range_by_call_id = {call_range.call_id: call_range for call_range in ranges}
    projection_by_call_id: dict[str, RequestProjection] = {}
    for row in rows:
        call_id = str(row["call_id"])
        call_range = range_by_call_id.get(call_id)
        if call_range is None or parse_db_ts(row["request_ts"]) > call_range.last_ts:
            continue
        row_values = dict(row)
        row_values["first_ts"] = call_range.first_ts
        projection_by_call_id.setdefault(call_id, _projection_from_row(row_values))
    return [
        projection_by_call_id[call_range.call_id]
        for call_range in ranges
        if call_range.call_id in projection_by_call_id
    ]


async def _fetch_request_messages_by_call_id(
    conn: Any, session_id: str, call_ids: Sequence[str], *, is_sqlite: bool
) -> dict[str, list[dict[str, Any]]]:
    if not call_ids:
        return {}
    if is_sqlite:
        placeholders = ", ".join(f"${index}" for index in range(2, len(call_ids) + 2))
        rows = await conn.fetch(
            f"""
            SELECT call_id, json_extract(payload, '$.final_request.messages') AS messages
            FROM conversation_events
            WHERE session_id = $1 AND call_id IN ({placeholders}) AND event_type = 'transaction.request_recorded'
            ORDER BY call_id, created_at ASC
            """,
            session_id,
            *call_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT call_id, payload->'final_request'->'messages' AS messages
            FROM conversation_events
            WHERE session_id = $1 AND call_id = ANY($2) AND event_type = 'transaction.request_recorded'
            ORDER BY call_id, created_at ASC
            """,
            session_id,
            list(call_ids),
        )
    messages_by_call_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        call_id = str(row["call_id"])
        messages_by_call_id.setdefault(call_id, _json_list(row["messages"]))
    return messages_by_call_id


async def _fetch_call_ranges(session_id: str, db_pool: DatabasePool) -> list[CallEventRange]:
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT call_id, MIN(created_at) as first_ts, MAX(created_at) as last_ts
            FROM conversation_events
            WHERE session_id = $1
            GROUP BY call_id
            ORDER BY MIN(created_at) ASC
            """,
            session_id,
        )
    if not rows:
        raise ValueError(f"No events found for session_id: {session_id}")
    return [
        CallEventRange(
            call_id=str(row["call_id"]),
            first_ts=parse_db_ts(row["first_ts"]),
            last_ts=parse_db_ts(row["last_ts"]),
        )
        for row in rows
    ]


async def iter_session_turns(
    session_id: str,
    db_pool: DatabasePool,
    ranges: list[CallEventRange] | None = None,
    *,
    initial_prev_real_msg_count: int = 0,
    projection_offset: int | None = None,
    projection_limit: int | None = None,
) -> AsyncIterator[ConversationTurn]:
    """Yield conversation turns with request messages reduced to transcript deltas."""
    if ranges is None:
        ranges = await _fetch_call_ranges(session_id, db_pool)
    is_sqlite = db_pool.is_sqlite is True
    async with db_pool.connection() as conn:
        async with conn.transaction():
            projections = await _fetch_request_projections(
                conn,
                session_id,
                ranges,
                is_sqlite=is_sqlite,
                offset=projection_offset,
                limit=projection_limit,
            )
            plan = _select_fast_path_anchor(projections)
            if plan is not None and len(projections) == len(ranges):
                messages_by_call_id = await _fetch_request_messages_by_call_id(
                    conn,
                    session_id,
                    [plan.anchor.call_id, *(projection.call_id for projection in plan.preflights)],
                    is_sqlite=is_sqlite,
                )
                event_rows_by_call_id = await _fetch_non_request_event_rows(
                    conn,
                    session_id,
                    ranges,
                    is_sqlite=is_sqlite,
                )
                events_by_call_id = {
                    call_id: _stored_events_from_rows(rows) for call_id, rows in event_rows_by_call_id.items()
                }
                async for turn in _iter_session_turns_fast(
                    projections,
                    events_by_call_id,
                    messages_by_call_id,
                    initial_prev_real_msg_count=initial_prev_real_msg_count,
                ):
                    yield turn
                return

    async for turn in _iter_session_turns_slow(
        session_id,
        db_pool,
        ranges,
        initial_prev_real_msg_count=initial_prev_real_msg_count,
    ):
        yield turn


async def _iter_session_turns_slow(
    session_id: str,
    db_pool: DatabasePool,
    ranges: list[CallEventRange],
    *,
    initial_prev_real_msg_count: int = 0,
) -> AsyncIterator[ConversationTurn]:
    delta_state = RequestDeltaState(prev_real_msg_count=initial_prev_real_msg_count)
    async with db_pool.connection() as conn:
        async with conn.transaction():
            for batch in _range_batches(ranges):
                rows_by_call_id = await _fetch_turn_batch_rows(
                    conn,
                    session_id,
                    batch,
                    is_sqlite=db_pool.is_sqlite is True,
                )
                for call_range in batch:
                    full_turn = _build_turn(
                        call_range.call_id,
                        _stored_events_from_rows(rows_by_call_id[call_range.call_id]),
                    )
                    turn, delta_state = _apply_request_delta(full_turn, delta_state)
                    yield turn


async def _streaming_detail_stats(
    session_id: str, db_pool: DatabasePool
) -> tuple[list[CallEventRange], int, list[str]]:
    ranges = await _fetch_call_ranges(session_id, db_pool)
    total_interventions = 0
    models: set[str] = set()
    async for turn in iter_session_turns(session_id, db_pool, ranges):
        if turn.model:
            models.add(turn.model)
        if turn.had_policy_intervention:
            total_interventions += len(turn.annotations)
    return ranges, total_interventions, sorted(models)


async def stream_session_detail_json(
    session_id: str,
    db_pool: DatabasePool,
    *,
    offset: int | None = None,
    limit: int = _SESSION_DETAIL_DEFAULT_LIMIT,
) -> AsyncIterator[bytes]:
    """Stream the session detail JSON response without materializing all turns."""
    window = await _fetch_session_turn_window(session_id, db_pool, offset=offset, limit=limit)
    stats = await _fetch_session_detail_stats(session_id, db_pool)
    # Detail/JSONL may truncate after a committed 200 if a per-call read fails;
    # markdown computes header stats before yielding, so the same failure is pre-response.
    yield b'{"session_id":"' + json.dumps(session_id).encode()[1:-1] + b'",'
    yield b'"first_timestamp":"' + window.first_timestamp.isoformat().encode() + b'",'
    yield b'"last_timestamp":"' + window.last_timestamp.isoformat().encode() + b'",'
    yield b'"turns":['
    first_turn = True
    async for turn in iter_session_turns(
        session_id,
        db_pool,
        window.ranges,
        initial_prev_real_msg_count=window.initial_prev_real_msg_count,
        projection_offset=window.offset,
        projection_limit=window.limit,
    ):
        if not first_turn:
            yield b","
        first_turn = False
        yield turn.model_dump_json().encode()
    yield b'],"total_policy_interventions":' + str(stats.total_policy_interventions).encode() + b","
    yield b'"models_used":' + json.dumps(stats.models_used).encode() + b","
    yield b'"total_turns":' + str(window.total_turns).encode() + b","
    yield b'"offset":' + str(window.offset).encode() + b","
    yield b'"limit":' + str(window.limit).encode() + b","
    yield b'"has_more":' + (b"true" if window.offset > 0 else b"false") + b"}"


async def stream_session_markdown(session_id: str, db_pool: DatabasePool) -> AsyncIterator[str]:
    """Stream the markdown export without materializing all turns."""
    ranges, total_interventions, models = await _streaming_detail_stats(session_id, db_pool)
    yield f"# Conversation History: {session_id}\n"
    yield "\n"
    yield f"**Started:** {ranges[0].first_ts.isoformat()}\n"
    yield f"**Ended:** {_last_timestamp_from_ranges(ranges).isoformat()}\n"
    yield f"**Turns:** {len(ranges)}\n"
    if models:
        yield f"**Models:** {', '.join(models)}\n"
    if total_interventions > 0:
        yield f"**Policy Interventions:** {total_interventions}\n"
    yield "\n---\n"
    yield "\n"
    turn_number = 1
    async for turn in iter_session_turns(session_id, db_pool, ranges):
        yield f"## Turn {turn_number}\n"
        if turn.model:
            yield f"*Model: {turn.model}*\n"
        yield "\n"
        for msg in turn.request_messages:
            yield _format_message_markdown(msg)
            yield "\n\n"
        for msg in turn.response_messages:
            yield _format_message_markdown(msg)
            yield "\n\n"
        if turn.annotations:
            yield "### Policy Annotations\n"
            for ann in turn.annotations:
                yield f"- **{ann.policy_name}**: {ann.summary}\n"
            yield "\n"
        yield "---\n"
        if turn_number < len(ranges):
            yield "\n"
        turn_number += 1


async def stream_session_jsonl(session_id: str, db_pool: DatabasePool) -> AsyncIterator[str]:
    """Stream the JSONL export without materializing all turns."""
    ranges = await _fetch_call_ranges(session_id, db_pool)
    async for turn in iter_session_turns(session_id, db_pool, ranges):
        record: dict[str, object] = {
            "call_id": turn.call_id,
            "session_id": session_id,
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
        if turn.request_messages_full is not None:
            record["request_messages_full"] = [m.model_dump(mode="json") for m in turn.request_messages_full]
        if turn.original_response_messages is not None:
            record["original_response_messages"] = [m.model_dump(mode="json") for m in turn.original_response_messages]
        yield json.dumps(record, default=str) + "\n"


_REQUEST_PARAM_ALLOWLIST = (
    "model",
    "max_tokens",
    "stream",
    "temperature",
    "top_p",
    "top_k",
    "stop_sequences",
    "output_config",
)
_REQUEST_PARAM_NORMALIZERS = {
    "model": _raw_request_param,
    "max_tokens": _raw_request_param,
    "stream": _bool_request_param,
    "temperature": _raw_request_param,
    "top_p": _raw_request_param,
    "top_k": _raw_request_param,
    "stop_sequences": _json_list_request_param,
    "output_config": _output_config_request_param,
}


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
                raise KeyError("transaction.request_recorded missing 'final_request'")

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
        request_messages_full=request_messages if request_was_modified else None,
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
        if turn.request_messages_full is not None:
            record["request_messages_full"] = [m.model_dump(mode="json") for m in turn.request_messages_full]
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
    "iter_session_turns",
    "export_session_markdown",
    "export_session_jsonl",
    "stream_session_detail_json",
    "stream_session_markdown",
    "stream_session_jsonl",
]
