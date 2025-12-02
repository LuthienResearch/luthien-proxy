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


# === Trace Viewer Models ===


class SpanData(BaseModel):
    """Represents a span in the request trace timeline.

    Spans are hierarchical units of work with timing information.
    They can be nested (parent/child relationships) and contain
    attributes describing the operation.
    """

    span_id: str
    parent_span_id: str | None = None
    name: str
    start_time: str  # ISO timestamp
    end_time: str | None = None  # ISO timestamp
    duration_ms: float | None = None
    status: str = "ok"  # ok, error, unset
    kind: str = "internal"  # server, client, internal, producer, consumer
    attributes: dict[str, Any] = {}
    events: list[dict[str, Any]] = []  # Span events (logs attached to span)


class LogEntry(BaseModel):
    """Represents a log message in the trace timeline.

    Log entries are point-in-time messages that may or may not
    be associated with a specific span.
    """

    timestamp: str  # ISO timestamp
    level: str  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    message: str
    logger: str | None = None
    span_id: str | None = None
    trace_id: str | None = None
    attributes: dict[str, Any] = {}


class TimelineEvent(BaseModel):
    """A unified event in the trace timeline.

    Used to represent both conversation events and policy events
    in a consistent format for timeline visualization.
    """

    id: str
    timestamp: str  # ISO timestamp
    event_type: str
    category: str  # request, response, policy, system
    title: str
    description: str | None = None
    payload: dict[str, Any] = {}
    duration_ms: float | None = None


class TraceResponse(BaseModel):
    """Complete trace data for a call.

    Contains all the information needed to render a trace timeline:
    - Spans showing hierarchical request flow
    - Log entries showing messages over time
    - Timeline events showing key milestones
    - Metadata about the trace
    """

    call_id: str
    trace_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: float | None = None
    status: str = "unknown"  # success, error, unknown
    model: str | None = None
    provider: str | None = None

    spans: list[SpanData] = []
    logs: list[LogEntry] = []
    timeline_events: list[TimelineEvent] = []

    tempo_trace_url: str | None = None
    grafana_logs_url: str | None = None


__all__ = [
    "ConversationEventResponse",
    "CallEventsResponse",
    "MessageDiff",
    "RequestDiff",
    "ResponseDiff",
    "CallDiffResponse",
    "CallListItem",
    "CallListResponse",
    "SpanData",
    "LogEntry",
    "TimelineEvent",
    "TraceResponse",
]
