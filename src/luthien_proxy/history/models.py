"""Data models for conversation history viewer.

Defines Pydantic models for:
- Session summaries and listings
- Conversation turns with typed messages
- Policy annotations for interventions
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel


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


__all__ = [
    "MessageType",
    "PolicyAnnotation",
    "ConversationMessage",
    "ConversationTurn",
    "SessionSummary",
    "SessionListResponse",
    "SessionDetail",
]
