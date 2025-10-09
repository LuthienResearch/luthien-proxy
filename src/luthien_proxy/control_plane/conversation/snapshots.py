"""Call snapshot assembly for request/response schema."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List

from .models import ConversationCallSnapshot, ConversationEvent, ConversationMessageDiff
from .utils import clone_messages, normalize_status


def build_call_snapshots(events: Iterable[ConversationEvent]) -> list[ConversationCallSnapshot]:
    """Aggregate per-call snapshots from request/response events."""
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

    for call_id in call_order:
        call_events = events_by_call[call_id]
        trace_id = next((evt.trace_id for evt in call_events if evt.trace_id), None)

        request_messages: list[dict[str, str]] = []
        response_text: str = ""
        started_at: datetime | None = None
        completed_at: datetime | None = None
        status: str = "pending"

        for event in call_events:
            if started_at is None or event.timestamp < started_at:
                started_at = event.timestamp

            if event.event_type == "request":
                # Extract messages from OpenAI format request
                payload = event.payload
                messages_raw = payload.get("messages")
                if isinstance(messages_raw, list):
                    request_messages = clone_messages(messages_raw)

            elif event.event_type == "response":
                # Extract response from OpenAI format response
                payload = event.payload
                message = payload.get("message") if isinstance(payload, dict) else {}
                if not isinstance(message, dict):
                    message = {}

                # Extract text content
                content = message.get("content")
                if isinstance(content, str):
                    response_text = content

                # Update status and completion time
                status_raw = payload.get("status") if isinstance(payload, dict) else "success"
                status = str(status_raw) if status_raw else "success"
                completed_at = event.timestamp

        # Build message diffs - only include the last user message as "new"
        # The rest are conversation context
        new_messages: list[ConversationMessageDiff] = []
        if request_messages:
            # Find the last user message (the actual query for this turn)
            last_user_idx = -1
            for i in range(len(request_messages) - 1, -1, -1):
                if request_messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx >= 0:
                msg = request_messages[last_user_idx]
                content = str(msg.get("content", ""))
                new_messages.append(
                    ConversationMessageDiff(
                        role="user",
                        original=content,
                        final=content,
                    )
                )

        status_literal = normalize_status(status, chunk_count=1 if response_text else 0, completed_at=completed_at)

        # Create snapshot
        snapshot = ConversationCallSnapshot(
            call_id=call_id,
            trace_id=trace_id,
            started_at=started_at,
            completed_at=completed_at,
            status=status_literal,
            new_messages=new_messages,
            request_original_messages=clone_messages(request_messages),
            request_final_messages=clone_messages(request_messages),  # Same - no modification tracking
            original_response=response_text,
            final_response=response_text,  # Same - no modification tracking
            chunk_count=1 if response_text else 0,
            original_chunks=[response_text] if response_text else [],
            final_chunks=[response_text] if response_text else [],
        )
        snapshots.append(snapshot)

    return snapshots


__all__ = ["build_call_snapshots"]
