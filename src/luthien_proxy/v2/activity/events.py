# ABOUTME: Event models for V2 activity stream.
# ABOUTME: Tracks request/response lifecycle and policy execution for real-time debugging.
"""Activity event models for V2 monitoring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActivityEvent(BaseModel):
    """Base model for all activity events.

    Each event represents a discrete moment in the request/response lifecycle.
    All events are published to Redis and streamed to the UI in real-time.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = Field(description="Type of event (request_received, response_sent, etc.)")
    call_id: str = Field(description="Unique identifier for this request/response pair")
    trace_id: str | None = Field(default=None, description="Optional trace ID for distributed tracing")

    model_config = {"extra": "forbid"}


class OriginalRequestReceived(ActivityEvent):
    """Event: Original request received from client (before policy processing)."""

    event_type: Literal["request_received"] = "request_received"
    endpoint: str = Field(description="API endpoint hit (e.g., /v1/chat/completions)")
    model: str = Field(description="Model requested by client")
    messages: list[dict[str, Any]] = Field(description="Messages from client")
    stream: bool = Field(description="Whether client requested streaming")
    api_key_hash: str = Field(description="Hash of client API key")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional request metadata")


class PolicyEventEmitted(ActivityEvent):
    """Event: Policy emitted an event during processing."""

    event_type: Literal["policy_event"] = "policy_event"
    policy_name: str = Field(description="Name of policy that emitted the event")
    event_name: str = Field(description="Name of the policy event")
    description: str = Field(description="Human-readable description")
    data: dict[str, Any] = Field(default_factory=dict, description="Event-specific data")
    phase: Literal["request", "response", "streaming"] = Field(description="Which phase emitted the event")


class FinalRequestSent(ActivityEvent):
    """Event: Final request sent to backend LLM (after policy processing)."""

    event_type: Literal["request_sent"] = "request_sent"
    model: str = Field(description="Model being called")
    messages: list[dict[str, Any]] = Field(description="Messages sent to backend")
    stream: bool = Field(description="Whether streaming was requested")
    modifications: list[str] = Field(default_factory=list, description="List of modifications made by policies")


class OriginalResponseReceived(ActivityEvent):
    """Event: Original non-streaming response received from backend (before policy processing)."""

    event_type: Literal["response_received"] = "response_received"
    model: str = Field(description="Model that generated response")
    content: str = Field(description="Response content")
    usage: dict[str, Any] | None = Field(default=None, description="Token usage stats")
    finish_reason: str | None = Field(default=None, description="Why generation stopped")


class OriginalResponseChunk(ActivityEvent):
    """Event: Original streaming response chunk received from backend."""

    event_type: Literal["response_chunk"] = "response_chunk"
    chunk_index: int = Field(description="Index of this chunk in the stream")
    delta_content: str | None = Field(default=None, description="Content delta in this chunk")
    finish_reason: str | None = Field(default=None, description="Finish reason if stream is ending")
    usage: dict[str, Any] | None = Field(default=None, description="Token usage if available")


class FinalResponseSent(ActivityEvent):
    """Event: Final non-streaming response sent to client (after policy processing)."""

    event_type: Literal["response_sent"] = "response_sent"
    content: str = Field(description="Response content sent to client")
    usage: dict[str, Any] | None = Field(default=None, description="Token usage stats")
    finish_reason: str | None = Field(default=None, description="Why generation stopped")
    modifications: list[str] = Field(default_factory=list, description="List of modifications made by policies")


class FinalResponseChunk(ActivityEvent):
    """Event: Final streaming response chunk sent to client (after policy processing)."""

    event_type: Literal["response_chunk_sent"] = "response_chunk_sent"
    chunk_index: int = Field(description="Index of this chunk in the stream")
    delta_content: str | None = Field(default=None, description="Content delta sent to client")
    finish_reason: str | None = Field(default=None, description="Finish reason if stream is ending")
    was_modified: bool = Field(default=False, description="Whether this chunk was modified by policy")
