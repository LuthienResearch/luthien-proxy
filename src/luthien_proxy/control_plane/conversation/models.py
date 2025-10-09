"""Pydantic models used across conversation tracing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from luthien_proxy.types import JSONObject


class TraceEntry(BaseModel):
    """A single hook event for a call ID, optionally with nanosecond time."""

    time: datetime
    post_time_ns: Optional[int] = None
    hook: Optional[str] = None
    debug_type: Optional[str] = None
    payload: JSONObject


class TraceResponse(BaseModel):
    """Ordered list of hook entries belonging to a call ID."""

    call_id: str
    entries: list[TraceEntry]
    offset: int
    limit: int
    has_more: bool
    next_offset: Optional[int] = None


class CallIdInfo(BaseModel):
    """Summary row for a recent litellm_call_id with counts and latest time."""

    call_id: str
    count: int
    latest: datetime


class TraceInfo(BaseModel):
    """Summary row for a litellm_trace_id with aggregates."""

    trace_id: str
    call_count: int
    event_count: int
    latest: datetime


class ConversationEvent(BaseModel):
    """Normalized conversation event derived from debug hooks."""

    call_id: str
    trace_id: Optional[str] = None
    event_type: Literal[
        "request_started",
        "original_chunk",
        "final_chunk",
        "request_completed",
    ]
    sequence: int
    timestamp: datetime
    hook: str
    payload: JSONObject = Field(default_factory=dict)


class ConversationMessageDiff(BaseModel):
    """Difference for a single request message between original and final forms."""

    role: str
    original: str
    final: str


class ConversationCallSnapshot(BaseModel):
    """Canonical view of a single request/response within a trace."""

    call_id: str
    trace_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: Literal["pending", "success", "stream_summary", "failure", "streaming"] = "pending"
    new_messages: list[ConversationMessageDiff] = Field(default_factory=list)
    request_original_messages: list[dict[str, str]] = Field(default_factory=list)
    request_final_messages: list[dict[str, str]] = Field(default_factory=list)
    original_response: str = ""
    final_response: str = ""
    chunk_count: int = 0
    original_chunks: list[str] = Field(default_factory=list)
    final_chunks: list[str] = Field(default_factory=list)


class ConversationSnapshot(BaseModel):
    """Snapshot of a single call with its normalized events."""

    call_id: str
    trace_id: Optional[str] = None
    events: list[ConversationEvent] = Field(default_factory=list)
    calls: list[ConversationCallSnapshot] = Field(default_factory=list)


class TraceConversationSnapshot(BaseModel):
    """Snapshot of a trace spanning one or more calls."""

    trace_id: str
    call_ids: list[str] = Field(default_factory=list)
    events: list[ConversationEvent] = Field(default_factory=list)
    calls: list[ConversationCallSnapshot] = Field(default_factory=list)


__all__ = [
    "TraceEntry",
    "TraceResponse",
    "CallIdInfo",
    "TraceInfo",
    "ConversationEvent",
    "ConversationMessageDiff",
    "ConversationCallSnapshot",
    "ConversationSnapshot",
    "TraceConversationSnapshot",
]
