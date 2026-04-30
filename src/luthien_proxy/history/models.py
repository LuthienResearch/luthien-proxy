"""Data models for conversation history viewer.

Defines Pydantic models for:
- Session summaries and listings
- Conversation turns with typed messages
- Policy annotations for interventions
- Search parameters for server-side session filtering
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, model_validator


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
    user_id: str | None = None  # User identity extracted from X-Luthien-User-Id or JWT sub claim


class SessionListResponse(BaseModel):
    """Response for session list endpoint."""

    sessions: list[SessionSummary]
    total: int  # Total count of all sessions in database
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


class SessionSearchParams(BaseModel):
    """Search/filter parameters for the session list endpoint.

    All fields are optional. When all are None/False, no filtering is applied
    and the endpoint behaves identically to the original unfiltered list.

    Attributes:
        user: Filter by user_id prefix (case-sensitive LIKE 'value%').
            Matches the user_id extracted from X-Luthien-User-Id header or JWT sub claim
            (requires TRUST_USER_ID_HEADER=true).
        model: Filter by model name (exact match, e.g. 'claude-opus-4-6').
        from_time: ISO 8601 lower bound on session last activity (inclusive).
        to_time: ISO 8601 upper bound on session last activity (inclusive).
        q: Full-text search on user message and assistant response text.
            PostgreSQL uses tsvector/GIN index; SQLite uses LIKE (slow for large datasets).
        policy_intervention: If True, only return sessions with at least one
            policy intervention. If None or False, no filter applied.
    """

    user: str | None = None
    model: str | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None
    q: str | None = None
    policy_intervention: bool | None = None

    @model_validator(mode="after")
    def _validate_time_range(self) -> "SessionSearchParams":
        if self.from_time is not None and self.to_time is not None:
            if self.from_time > self.to_time:
                raise ValueError("from_time must be before or equal to to_time")
        return self

    def is_empty(self) -> bool:
        """Return True if no filters are set (all fields are None or False)."""
        return (
            self.user is None
            and self.model is None
            and self.from_time is None
            and self.to_time is None
            and self.q is None
            and not self.policy_intervention
        )


__all__ = [
    "MessageType",
    "PolicyAnnotation",
    "ConversationMessage",
    "ConversationTurn",
    "SessionSummary",
    "SessionListResponse",
    "SessionDetail",
    "SessionSearchParams",
]
