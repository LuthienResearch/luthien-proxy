# ABOUTME: Data models for control plane interface using Pydantic
# ABOUTME: Protocol-agnostic models that work with both local and networked implementations

"""Data models for control plane interface."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StreamAction(Enum):
    """Actions that can be returned from streaming policies."""

    CONTINUE = "continue"
    ABORT = "abort"
    SWITCH_MODEL = "switch_model"


class RequestMetadata(BaseModel):
    """Metadata about the request context.

    This is passed alongside the request data to give the control plane
    context about who is making the request, when, and how to track it.
    """

    call_id: str
    timestamp: datetime
    api_key_hash: str
    trace_id: Optional[str] = None
    user_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class PolicyEvent(BaseModel):
    """Event emitted by a policy to describe its activity.

    Policies emit these events to:
    - Log their decisions and reasoning
    - Provide visibility into policy execution
    - Enable debugging and auditing
    - Feed the activity stream UI
    """

    event_type: str = Field(
        description="Type of event (e.g., 'request_modified', 'content_filtered', 'stream_aborted')"
    )
    call_id: str = Field(description="Call ID this event relates to")
    timestamp: datetime = Field(default_factory=lambda: datetime.now())
    summary: str = Field(description="Human-readable summary of what happened")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional structured data about the event")
    severity: str = Field(default="info", description="Severity level: debug, info, warning, error")

    model_config = {"extra": "forbid"}


class StreamingContext(BaseModel):
    """Context for streaming operations.

    This is created at the start of a stream and passed to each chunk handler.
    It maintains state across chunks for a single streaming request.
    """

    stream_id: str
    call_id: str
    request_data: dict[str, Any]
    policy_state: dict[str, Any] = Field(default_factory=dict)
    chunk_count: int = 0
    should_abort: bool = False


__all__ = [
    "RequestMetadata",
    "PolicyEvent",
    "StreamingContext",
    "StreamAction",
]
