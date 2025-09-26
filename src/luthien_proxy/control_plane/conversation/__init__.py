"""Utilities and data structures for conversation tracing."""

from .db import (
    fetch_trace_entries,
    fetch_trace_entries_by_trace,
)
from .events import build_conversation_events, events_from_trace_entries
from .models import (
    CallIdInfo,
    ConversationCallSnapshot,
    ConversationEvent,
    ConversationMessageDiff,
    ConversationSnapshot,
    TraceConversationSnapshot,
    TraceEntry,
    TraceInfo,
    TraceResponse,
)
from .snapshots import build_call_snapshots
from .streams import (
    conversation_sse_stream,
    conversation_sse_stream_by_trace,
    publish_conversation_event,
    publish_trace_conversation_event,
)
from .utils import json_safe, strip_post_time_ns

__all__ = [
    "CallIdInfo",
    "ConversationCallSnapshot",
    "ConversationEvent",
    "ConversationMessageDiff",
    "ConversationSnapshot",
    "TraceConversationSnapshot",
    "TraceEntry",
    "TraceInfo",
    "TraceResponse",
    "build_conversation_events",
    "events_from_trace_entries",
    "build_call_snapshots",
    "publish_conversation_event",
    "publish_trace_conversation_event",
    "conversation_sse_stream",
    "conversation_sse_stream_by_trace",
    "fetch_trace_entries",
    "fetch_trace_entries_by_trace",
    "json_safe",
    "strip_post_time_ns",
]
