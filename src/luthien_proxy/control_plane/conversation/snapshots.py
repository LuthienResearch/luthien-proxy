"""Call snapshot assembly."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List

from .models import ConversationCallSnapshot, ConversationEvent, ConversationMessageDiff
from .utils import clone_messages, message_equals, normalize_status


def build_call_snapshots(events: Iterable[ConversationEvent]) -> list[ConversationCallSnapshot]:
    """Aggregate per-call snapshots from a sequence of conversation events."""
    ordered_events = sorted(
        events,
        key=lambda evt: (evt.sequence, evt.timestamp, evt.event_type),
    )
    events_by_call: Dict[str, List[ConversationEvent]] = {}
    call_order: List[str] = []
    for event in ordered_events:
        bucket = events_by_call.setdefault(event.call_id, [])
        if not bucket:
            call_order.append(event.call_id)
        bucket.append(event)

    snapshots: list[ConversationCallSnapshot] = []
    conversation_context: list[dict[str, str]] = []

    for call_id in call_order:
        call_events = events_by_call[call_id]
        trace_id = next((evt.trace_id for evt in call_events if evt.trace_id), None)
        request_original: list[dict[str, str]] = []
        request_final: list[dict[str, str]] = []
        original_chunks: list[str] = []
        final_chunks: list[str] = []
        started_at: datetime | None = None
        completed_at: datetime | None = None
        status: str = "pending"

        for event in call_events:
            if started_at is None or event.timestamp < started_at:
                started_at = event.timestamp

            if event.event_type == "request_started":
                payload = event.payload
                original_raw = payload.get("original_messages")
                final_raw = payload.get("final_messages")
                original_messages = original_raw if isinstance(original_raw, list) else []
                final_messages = final_raw if isinstance(final_raw, list) else []
                request_original = clone_messages(original_messages)
                request_final = clone_messages(final_messages) if final_messages else clone_messages(original_messages)
                original_chunks = []
                final_chunks = []

            elif event.event_type == "original_chunk":
                delta = str(event.payload.get("delta") or "")
                chunk_index = event.payload.get("chunk_index")
                if chunk_index is not None and isinstance(chunk_index, int):
                    while len(original_chunks) <= chunk_index:
                        original_chunks.append("")
                    original_chunks[chunk_index] = delta
                elif delta:
                    original_chunks.append(delta)

            elif event.event_type == "final_chunk":
                delta = str(event.payload.get("delta") or "")
                chunk_index = event.payload.get("chunk_index")
                if chunk_index is not None and isinstance(chunk_index, int):
                    while len(final_chunks) <= chunk_index:
                        final_chunks.append("")
                    final_chunks[chunk_index] = delta
                elif delta:
                    final_chunks.append(delta)

            elif event.event_type == "request_completed":
                payload = event.payload
                status = str(payload.get("status") or "success") or "success"
                original_text = str(payload.get("original_response") or "")
                final_text = str(payload.get("final_response") or "")
                if original_text:
                    original_chunks = [original_text]
                if final_text:
                    final_chunks = [final_text]
                completed_at = event.timestamp

                # If we don't have request messages from request_started (no call_id in pre_call_hook),
                # extract them from the request_messages field in the completion event
                if not request_original and not request_final:
                    request_messages_raw = payload.get("request_messages")
                    if isinstance(request_messages_raw, list):
                        request_original = clone_messages(request_messages_raw)
                        request_final = clone_messages(request_messages_raw)

        original_response = "".join(original_chunks)
        final_response = "".join(final_chunks) or original_response

        if not original_chunks and original_response:
            original_chunks = [original_response]
        if not final_chunks and final_response:
            final_chunks = [final_response]

        chunk_count = len(final_chunks)
        status_literal = normalize_status(status, chunk_count=chunk_count, completed_at=completed_at)

        baseline = conversation_context
        effective_final_messages = request_final or request_original
        max_len = max(len(request_original), len(effective_final_messages), len(baseline))
        message_diffs: list[ConversationMessageDiff] = []
        for idx in range(max_len):
            original_msg = request_original[idx] if idx < len(request_original) else None
            final_msg = effective_final_messages[idx] if idx < len(effective_final_messages) else None
            baseline_msg = baseline[idx] if idx < len(baseline) else None

            role = (final_msg or original_msg or baseline_msg or {"role": "unknown"}).get("role", "unknown")
            original_text = original_msg["content"] if original_msg else ""
            final_text = final_msg["content"] if final_msg else original_text

            if baseline_msg and final_msg and message_equals(final_msg, baseline_msg):
                if original_msg is None or message_equals(original_msg, baseline_msg):
                    continue

            if not original_text and not final_text:
                continue

            message_diffs.append(
                ConversationMessageDiff(
                    role=role,
                    original=original_text,
                    final=final_text,
                )
            )

        snapshots.append(
            ConversationCallSnapshot(
                call_id=call_id,
                trace_id=trace_id,
                started_at=started_at,
                completed_at=completed_at,
                status=status_literal,
                new_messages=message_diffs,
                request_original_messages=clone_messages(request_original),
                request_final_messages=clone_messages(request_final or request_original),
                original_response=original_response,
                final_response=final_response,
                chunk_count=chunk_count,
                original_chunks=original_chunks,
                final_chunks=final_chunks,
            )
        )

        next_context = clone_messages(effective_final_messages)
        if final_response:
            next_context.append({"role": "assistant", "content": final_response})
        elif original_response:
            next_context.append({"role": "assistant", "content": original_response})
        conversation_context = next_context

    return snapshots


__all__ = ["build_call_snapshots"]
