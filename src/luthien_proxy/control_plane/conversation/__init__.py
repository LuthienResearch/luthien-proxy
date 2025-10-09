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
    load_conversation_turns,
    load_events_for_call,
    load_events_for_trace,
    load_recent_calls,
    load_recent_traces,
    load_tool_call_records,
)
from .events import build_conversation_events, clear_stream_indices, reset_stream_indices
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
from .store import record_conversation_events
from .streams import (
    ConversationStreamConfig,
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
    "build_call_snapshots",
    "publish_conversation_event",
    "publish_trace_conversation_event",
    "conversation_sse_stream",
    "conversation_sse_stream_by_trace",
    "ConversationStreamConfig",
    "record_conversation_events",
    "load_events_for_call",
    "load_recent_calls",
    "fetch_trace_entries",
    "load_events_for_trace",
    "load_recent_traces",
    "load_conversation_turns",
    "load_tool_call_records",
    "reset_stream_indices",
    "clear_stream_indices",
    "json_safe",
    "strip_post_time_ns",
]
