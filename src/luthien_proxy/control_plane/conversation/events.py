"""Conversation event builders."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Dict, Iterable, Literal, Optional

from .models import ConversationEvent, TraceEntry
from .utils import (
    JSONValue,
    delta_from_chunk,
    derive_sequence_ns,
    extract_choice_index,
    extract_response_text,
    extract_stream_chunk,
    extract_trace_id,
    format_messages,
    messages_from_payload,
    require_dict,
    unwrap_response,
)


class _StreamIndexStore:
    """Track stream chunk counters per call under a re-entrant lock."""

    def __init__(self) -> None:
        self._indices: Dict[str, Dict[str, int]] = {}
        self._lock = threading.RLock()

    def reset(self, call_id: str) -> None:
        with self._lock:
            self._indices[call_id] = {"original": 0, "final": 0}

    def next_index(self, call_id: str, stream: Literal["original", "final"]) -> int:
        with self._lock:
            state = self._indices.setdefault(call_id, {"original": 0, "final": 0})
            current = state[stream]
            state[stream] = current + 1
            return current

    def clear(self, call_id: str) -> None:
        with self._lock:
            self._indices.pop(call_id, None)


_stream_indices = _StreamIndexStore()


def reset_stream_indices(call_id: str) -> None:
    """Initialise per-stream chunk indices for a call."""
    _stream_indices.reset(call_id)


def next_chunk_index(call_id: str, stream: Literal["original", "final"]) -> int:
    """Return and advance the next chunk index for the given stream."""
    return _stream_indices.next_index(call_id, stream)


def clear_stream_indices(call_id: str) -> None:
    """Forget chunk indices for a completed call."""
    _stream_indices.clear(call_id)


# TODO: refactor this logic, it's a mess
def build_conversation_events(
    hook: str,
    call_id: Optional[str],
    trace_id: Optional[str],
    original: JSONValue | None,
    result: JSONValue | None,
    timestamp_ns_fallback: int,
    timestamp: datetime,
) -> list[ConversationEvent]:
    """Translate a hook invocation into one or more conversation events."""
    if not isinstance(call_id, str) or not call_id:
        return []

    effective_trace_id = trace_id
    if effective_trace_id is None and isinstance(original, dict):
        effective_trace_id = extract_trace_id(original)
    if effective_trace_id is None and isinstance(result, dict):
        effective_trace_id = extract_trace_id(result)

    sequence_ns = derive_sequence_ns(timestamp_ns_fallback, original, result)
    events: list[ConversationEvent] = []

    if hook == "async_pre_call_hook":
        original_payload = require_dict(original, "pre-call original payload")
        result_payload = require_dict(result, "pre-call result payload") if result is not None else original_payload
        originals = messages_from_payload(original_payload)
        finals = messages_from_payload(result_payload)
        reset_stream_indices(call_id)
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_started",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload={
                    "original_messages": format_messages(originals),
                    "final_messages": format_messages(finals),
                    "raw_original": original_payload,
                    "raw_result": result_payload,
                },
            )
        )
        return events

    if hook == "async_post_call_streaming_iterator_hook":
        original_chunk = extract_stream_chunk(original)
        final_chunk = extract_stream_chunk(result)
        source_for_index = final_chunk if final_chunk is not None else original_chunk
        if source_for_index is None:
            return events
        try:
            choice_index = extract_choice_index(source_for_index)
        except ValueError:
            choice_index = 0

        if original_chunk is not None:
            original_delta = delta_from_chunk(original_chunk)
            chunk_index = next_chunk_index(call_id, "original")
            events.append(
                ConversationEvent(
                    call_id=call_id,
                    trace_id=effective_trace_id,
                    event_type="original_chunk",
                    sequence=sequence_ns,
                    timestamp=timestamp,
                    hook=hook,
                    payload={
                        "chunk_index": chunk_index,
                        "delta": original_delta,
                        "choice_index": choice_index,
                        "raw_chunk": original_chunk,
                        "raw_payload": original,
                    },
                )
            )

        if final_chunk is not None:
            final_delta = delta_from_chunk(final_chunk)
            chunk_index = next_chunk_index(call_id, "final")
            events.append(
                ConversationEvent(
                    call_id=call_id,
                    trace_id=effective_trace_id,
                    event_type="final_chunk",
                    sequence=sequence_ns + 1,
                    timestamp=timestamp,
                    hook=hook,
                    payload={
                        "chunk_index": chunk_index,
                        "delta": final_delta,
                        "choice_index": choice_index,
                        "raw_chunk": final_chunk,
                        "raw_payload": result,
                    },
                )
            )
        return events

    if hook == "async_post_call_success_hook":
        original_response = unwrap_response(original)
        final_response = unwrap_response(result) if result is not None else None
        try:
            original_text = extract_response_text(original_response)
        except Exception:
            original_text = ""
        try:
            final_text = extract_response_text(final_response) if final_response is not None else ""
        except Exception:
            final_text = ""
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_completed",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload={
                    "status": "success",
                    "original_response": original_text,
                    "final_response": final_text,
                    "raw_original": original_response,
                    "raw_result": final_response,
                },
            )
        )
        clear_stream_indices(call_id)
        return events

    if hook == "async_post_call_streaming_hook":
        summary_payload = result if result is not None else original
        summary_response = unwrap_response(summary_payload)
        final_text = ""
        try:
            final_text = extract_response_text(summary_response)
        except Exception:
            final_text = ""
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_completed",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload={
                    "status": "stream_summary",
                    "final_response": final_text,
                    "raw_original": original,
                    "raw_result": result,
                },
            )
        )
        clear_stream_indices(call_id)
        return events

    if hook == "async_post_call_failure_hook":
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_completed",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload={
                    "status": "failure",
                    "raw_original": original,
                    "raw_result": result,
                },
            )
        )
        clear_stream_indices(call_id)
        return events

    return events


def events_from_trace_entry(entry: TraceEntry) -> list[ConversationEvent]:
    """Reconstruct conversation events from a stored debug log entry."""
    debug_type = entry.debug_type or ""
    if not debug_type.startswith("hook_result:"):
        return []

    hook = debug_type.split(":", 1)[1]
    payload = require_dict(entry.payload, "trace entry payload")
    original = payload.get("original")
    result = payload.get("result")
    call_id = payload.get("litellm_call_id")
    trace_id = payload.get("litellm_trace_id")
    timestamp_ns = entry.post_time_ns if entry.post_time_ns is not None else int(entry.time.timestamp() * 1_000_000_000)
    timestamp = entry.time

    effective_result = result if result is not None else original
    return build_conversation_events(
        hook=hook,
        call_id=call_id,
        trace_id=trace_id,
        original=original,
        result=effective_result,
        timestamp_ns_fallback=timestamp_ns,
        timestamp=timestamp,
    )


def events_from_trace_entries(entries: Iterable[TraceEntry]) -> list[ConversationEvent]:
    """Flatten and order events derived from a sequence of log entries."""
    collected: list[ConversationEvent] = []
    for entry in entries:
        collected.extend(events_from_trace_entry(entry))
    collected.sort(key=lambda evt: (evt.sequence, evt.timestamp, evt.event_type))
    return collected


__all__ = [
    "build_conversation_events",
    "reset_stream_indices",
    "next_chunk_index",
    "clear_stream_indices",
    "events_from_trace_entry",
    "events_from_trace_entries",
]
