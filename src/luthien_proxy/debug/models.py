# ABOUTME: Pydantic models for debug API responses
# ABOUTME: Shared data models used by routes and service layers

"""Data models for V2 debug API.

This module defines Pydantic models for:
- Individual conversation events
- Diffs between original and final requests/responses
- Call listing responses
"""

from typing import Any

from pydantic import BaseModel


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


__all__ = [
    "ConversationEventResponse",
    "CallEventsResponse",
    "MessageDiff",
    "RequestDiff",
    "ResponseDiff",
    "CallDiffResponse",
    "CallListItem",
    "CallListResponse",
]
