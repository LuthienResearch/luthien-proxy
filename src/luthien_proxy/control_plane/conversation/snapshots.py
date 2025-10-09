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

        request_original_messages: list[dict[str, str]] = []
        request_final_messages: list[dict[str, str]] = []
        original_response_text: str = ""
        final_response_text: str = ""
        started_at: datetime | None = None
        completed_at: datetime | None = None
        status: str = "pending"

        for event in call_events:
            if started_at is None or event.timestamp < started_at:
                started_at = event.timestamp

            if event.event_type == "request":
                # Extract messages from OpenAI format request - both original and final
                payload = event.payload

                # Try to get original and final from nested structure
                original_req = payload.get("original")
                final_req = payload.get("final")

                if isinstance(original_req, dict):
                    messages_raw = original_req.get("messages")
                    if isinstance(messages_raw, list):
                        request_original_messages = clone_messages(messages_raw)

                if isinstance(final_req, dict):
                    messages_raw = final_req.get("messages")
                    if isinstance(messages_raw, list):
                        request_final_messages = clone_messages(messages_raw)

                # Fallback to top-level messages for backwards compatibility
                if not request_original_messages and not request_final_messages:
                    messages_raw = payload.get("messages")
                    if isinstance(messages_raw, list):
                        request_original_messages = clone_messages(messages_raw)
                        request_final_messages = clone_messages(messages_raw)

            elif event.event_type == "response":
                # Extract response from OpenAI format response - both original and final
                payload = event.payload

                # Try to get original and final from nested structure
                original_resp = payload.get("original")
                final_resp = payload.get("final")

                if isinstance(original_resp, dict):
                    message = original_resp.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str):
                            original_response_text = content

                if isinstance(final_resp, dict):
                    message = final_resp.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str):
                            final_response_text = content

                # Fallback to top-level message for backwards compatibility
                if not original_response_text and not final_response_text:
                    message = payload.get("message") if isinstance(payload, dict) else {}
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str):
                            original_response_text = content
                            final_response_text = content

                # Update status and completion time
                status_raw = payload.get("status") if isinstance(payload, dict) else "success"
                status = str(status_raw) if status_raw else "success"
                completed_at = event.timestamp

        # Build message diffs - only include the last user message as "new"
        # The rest are conversation context
        # Use final messages for finding the last user message
        new_messages: list[ConversationMessageDiff] = []
        messages_to_check = request_final_messages if request_final_messages else request_original_messages
        if messages_to_check:
            # Find the last user message (the actual query for this turn)
            last_user_idx = -1
            for i in range(len(messages_to_check) - 1, -1, -1):
                if messages_to_check[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx >= 0:
                # Get original and final content for this message
                original_content = ""
                final_content = ""

                if request_original_messages and last_user_idx < len(request_original_messages):
                    msg = request_original_messages[last_user_idx]
                    original_content = str(msg.get("content", ""))

                if request_final_messages and last_user_idx < len(request_final_messages):
                    msg = request_final_messages[last_user_idx]
                    final_content = str(msg.get("content", ""))

                # If we only have one version, use it for both
                if not original_content:
                    original_content = final_content
                if not final_content:
                    final_content = original_content

                new_messages.append(
                    ConversationMessageDiff(
                        role="user",
                        original=original_content,
                        final=final_content,
                    )
                )

        status_literal = normalize_status(
            status, chunk_count=1 if final_response_text or original_response_text else 0, completed_at=completed_at
        )

        # Create snapshot with actual original and final versions
        snapshot = ConversationCallSnapshot(
            call_id=call_id,
            trace_id=trace_id,
            started_at=started_at,
            completed_at=completed_at,
            status=status_literal,
            new_messages=new_messages,
            request_original_messages=request_original_messages,
            request_final_messages=request_final_messages,
            original_response=original_response_text,
            final_response=final_response_text,
            chunk_count=1 if final_response_text or original_response_text else 0,
            original_chunks=[original_response_text] if original_response_text else [],
            final_chunks=[final_response_text] if final_response_text else [],
        )
        snapshots.append(snapshot)

    return snapshots


__all__ = ["build_call_snapshots"]
