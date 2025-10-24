# ABOUTME: Storage module for V2 event persistence
# ABOUTME: Handles conversation event emission to background queue

"""V2 Storage - Event emission and persistence helpers.

This module provides helpers for V2 to emit conversation events
to the same conversation_events table used by V1, but without
depending on LiteLLM callbacks.
"""

from .events import emit_request_event, emit_response_event

__all__ = [
    "emit_request_event",
    "emit_response_event",
]
