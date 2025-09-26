"""Conversation tracking and management module.

This module provides the core functionality for tracking and managing LLM conversation data:

- Event Building: Converting hook invocations into structured conversation events
- Snapshot Assembly: Building conversation snapshots from stored events
- Streaming: Server-sent events (SSE) streaming of conversation data
- Database Operations: Persistence and retrieval of conversation traces

The conversation system uses an append-only design with monotonic chunk indices
to ensure reliable streaming and state management. Each conversation call has
separate buffers for original and final content, allowing for policy modifications
to be tracked independently.

Main Components:
- ConversationEvent: Core event model for tracking conversation state changes
- ConversationSnapshot: Aggregated view of a conversation call
- ConversationCallSnapshot: Per-call summary with diffs and chunks
- Streaming: SSE endpoints for real-time conversation monitoring
"""

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
