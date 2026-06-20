"""Data models for conversation history viewer.

Defines Pydantic models for:
- Session summaries and listings
- Conversation turns with typed messages
- Policy annotations for interventions
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MessageType(str, Enum):
    """Type of message in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    UNKNOWN = "unknown"


class PolicyAnnotation(BaseModel):
    """Annotation for a policy intervention on a message or turn."""

    policy_name: str
    event_type: str
    summary: str
    details: dict[str, Any] | None = None


class ConversationMessage(BaseModel):
    """A single message in a conversation."""

    message_type: MessageType
    content: str
    # Tool-specific fields
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_input: dict[str, object] | None = None
    is_error: bool | None = None


class ConversationTurn(BaseModel):
    """A turn in the conversation (request + response pair)."""

    call_id: str
    timestamp: str
    model: str | None = None
    # Messages in this turn (from final request/response)
    request_messages: list[ConversationMessage]
    request_messages_full: list[ConversationMessage] | None = None
    response_messages: list[ConversationMessage]
    # Policy annotations for this turn
    annotations: list[PolicyAnnotation]
    # Whether anything was modified by policy
    had_policy_intervention: bool = False
    # Turn-level modification tracking
    request_was_modified: bool = False
    response_was_modified: bool = False
    original_request_messages: list[ConversationMessage] | None = None
    original_response_messages: list[ConversationMessage] | None = None
    # Request params (everything except messages/system, which are already parsed)
    request_params: dict[str, Any] | None = None


class SessionSummary(BaseModel):
    """Summary of a session for list view."""

    session_id: str
    first_timestamp: str
    last_timestamp: str
    turn_count: int
    total_events: int
    policy_interventions: int
    models_used: list[str]
    preview_message: str | None = None  # Preview of session (last user message, truncated)
    # user_ids are user-asserted (JWT signature not verified) — attribution only, not authentication.
    # Distinct user_ids attributed to calls in this session:
    #   - empty list: no call carried a user_id (TRUST_USER_ID_HEADER off and no JWT) — common default.
    #   - one element: standard case.
    #   - multi-element: session reused across users (e.g. shared frontend session_id, rotating JWTs).
    user_ids: list[str] = Field(default_factory=list)  # X-Luthien-User-Id or JWT sub claim


class SessionSearchParams(BaseModel):
    """Optional server-side filters for the session list endpoint.

    All fields default to "no filter". When several are set they combine with
    AND. A session is the unit of matching: model/q ask "does any event in this
    session match?", while the time and intervention filters operate on
    session-level aggregates.

    Semantics:
      - model: session used this exact ``final_model`` in at least one turn.
      - from_time / to_time: the session's *last activity* (the max event
        timestamp) falls within ``[from_time, to_time]``, inclusive. Either
        bound may be omitted. (Last-activity, not overlap — consistent with the
        list's ``ORDER BY last_ts``.) Bounds are interpreted as UTC: a
        timezone-aware value is converted to UTC and a naive value is taken
        as-is (see the validator below). When ``user_id`` is also set, "last
        activity" is scoped to *that user's* events in the session, not the
        session's last activity overall.
      - q: full-text content search over indexed conversation text. Postgres
        uses the ``search_vector`` tsvector column; SQLite uses the
        ``conversation_events_fts`` FTS5 table. Both are porter-stemmed and
        treat the query as a conjunction of terms (see ``utils.search``). A
        session matches if any of its events matches.
      - policy_intervention: when True, restrict to sessions with at least one
        policy intervention.

    Known limitation (tracked separately): the search corpus is built from
    ``final_request`` text, which includes gateway-injected ``<policy-context>``
    blocks. Queries for policy-context terms can therefore over-match on
    sessions that ran under an output-modifying policy.
    """

    model: str | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None
    q: str | None = None
    policy_intervention: bool = False

    @field_validator("from_time", "to_time")
    @classmethod
    def _to_naive_utc(cls, value: datetime | None) -> datetime | None:
        """Normalize time bounds to naive UTC.

        ``created_at`` is compared as a UTC timestamp (Postgres ``timestamptz``)
        / lexicographic ISO text (SQLite). A timezone-aware input is converted
        to UTC and stripped of tzinfo so comparison is consistent across
        backends; a naive input is left as-is (already assumed UTC).
        """
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def is_empty(self) -> bool:
        """True when no filter is active (the unfiltered list fast path applies)."""
        return (
            self.model is None
            and self.from_time is None
            and self.to_time is None
            and not (self.q and self.q.strip())
            and not self.policy_intervention
        )


class SessionListResponse(BaseModel):
    """Response for session list endpoint."""

    sessions: list[SessionSummary]
    total: int  # Total count of sessions matching the active filters (all sessions when unfiltered)
    offset: int = 0  # Current offset for pagination
    has_more: bool = False  # Whether there are more sessions after this page


class SessionDetail(BaseModel):
    """Full session detail for conversation view."""

    session_id: str
    first_timestamp: str
    last_timestamp: str
    turns: list[ConversationTurn]
    total_policy_interventions: int
    models_used: list[str]
    total_turns: int = 0
    offset: int = 0
    limit: int = 50
    has_more: bool = False


__all__ = [
    "MessageType",
    "PolicyAnnotation",
    "ConversationMessage",
    "ConversationTurn",
    "SessionSummary",
    "SessionSearchParams",
    "SessionListResponse",
    "SessionDetail",
]
