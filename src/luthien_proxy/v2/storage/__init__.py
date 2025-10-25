# ABOUTME: Storage module for V2 event persistence
# ABOUTME: Handles conversation event emission to background queue

"""V2 Storage - Event emission and persistence helpers.

This module provides helpers for V2 to emit conversation events
to the conversation_events database table.
"""

from .events import (
    emit_request_event,
    emit_response_event,
    reconstruct_full_response_from_chunks,
)
from .persistence import (
    CONVERSATION_EVENT_QUEUE,
    ConversationEvent,
    build_conversation_events,
    publish_conversation_event,
    record_conversation_events,
)

__all__ = [
    # Event emission (high-level API)
    "emit_request_event",
    "emit_response_event",
    "reconstruct_full_response_from_chunks",
    # Low-level persistence API
    "ConversationEvent",
    "CONVERSATION_EVENT_QUEUE",
    "build_conversation_events",
    "record_conversation_events",
    "publish_conversation_event",
]
