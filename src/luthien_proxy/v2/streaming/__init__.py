# ABOUTME: V2 streaming utilities and helpers
# ABOUTME: Provides event schema and gate helpers for policy authors

"""V2 streaming utilities.

This module provides helpers for writing streaming policies:
- events: Typed event schema over raw LiteLLM chunks (generic)
- gate: Tool-call-specific helper for judging policies

For generic streaming transformations, use iter_events() directly.
For tool-call judging, use ToolCallStreamGate for buffering and evaluation.
"""

from luthien_proxy.v2.streaming.events import (
    ContentChunk,
    OtherChunk,
    StreamClosed,
    StreamError,
    StreamEvent,
    StreamStarted,
    ToolCallComplete,
    ToolCallDelta,
    iter_events,
)
from luthien_proxy.v2.streaming.tool_call_stream_gate import GateDecision, ToolCall, ToolCallStreamGate

__all__ = [
    # Event-driven DSL
    # Events (generic - use these for any streaming policy)
    "StreamEvent",
    "StreamStarted",
    "ContentChunk",
    "ToolCallDelta",
    "ToolCallComplete",
    "OtherChunk",
    "StreamError",
    "StreamClosed",
    "iter_events",
    # Gate (specialized for tool-call judging policies)
    "ToolCall",
    "GateDecision",
    "ToolCallStreamGate",
]
