"""Pydantic models used across conversation tracing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from luthien_proxy.types import JSONObject


class CallIdInfo(BaseModel):
    """Summary row for a recent litellm_call_id with counts and latest time."""

    call_id: str
    count: int
    latest: datetime


class ConversationEvent(BaseModel):
    """Normalized conversation event (request or response)."""

    call_id: str
    trace_id: Optional[str] = None  # Deprecated, always None
    event_type: Literal["request", "response"]
    sequence: int
    timestamp: datetime
    hook: str  # Source hook that created this event
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


__all__ = [
    "CallIdInfo",
    "ConversationEvent",
    "ConversationMessageDiff",
    "ConversationCallSnapshot",
    "ConversationSnapshot",
]
