"""Incremental maintenance of the ``session_summaries`` materialized table.

``session_summaries`` denormalizes per-session aggregates (counts, models used,
a preview message, attributed user_id) so the history list page does not have
to re-aggregate ``conversation_events`` on every load.

It is updated *incrementally* from the per-event write path in
:mod:`luthien_proxy.observability.emitter` — one upsert per event — rather than
by a batched drain loop. That keeps the maintenance coupled to the existing
event-write transaction and avoids depending on the (separate) bounded
EventEmitter rework.

The SQL here is written to run unchanged on both Postgres and SQLite: the
SQLite connection wrapper translates ``$N`` placeholders and strips ``::``
casts, and ``ON CONFLICT ... DO UPDATE`` / ``COALESCE`` / ``MIN`` / ``MAX`` are
common to both backends.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from luthien_proxy.utils.db import ConnectionProtocol

# Preview is the first user-message text from the request that opened the
# session. Trimmed to keep the row small and the list page snappy.
PREVIEW_MAX_LENGTH = 200

# Claude Code injects <system-reminder>...</system-reminder> blocks into the
# first user turn; strip them so the preview shows the actual user text.
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _is_policy_event(event_type: str) -> bool:
    """True for policy-intervention events, excluding judge evaluations.

    Mirrors the backfill predicate in migration 021 so the incremental counter
    and the backfilled counter agree.
    """
    return event_type.startswith("policy.") and not event_type.startswith("policy.judge.evaluation")


def extract_model(data: dict[str, Any]) -> str | None:
    """Extract the final model name from a ``transaction.request_recorded`` payload."""
    model = data.get("final_model")
    return model if isinstance(model, str) and model else None


def extract_preview(data: dict[str, Any]) -> str | None:
    """Extract a short preview from the first user message of a request payload.

    Returns None for probe requests (``max_tokens <= 1``) and when no usable
    user text is present. The text is whitespace-collapsed and has
    ``<system-reminder>`` blocks stripped. When longer than
    ``PREVIEW_MAX_LENGTH`` it is cut at that many characters and a literal
    ``"..."`` ellipsis is appended (so the stored value can be up to
    ``PREVIEW_MAX_LENGTH + 3`` characters).
    """
    request = data.get("final_request") or data.get("original_request")
    if not isinstance(request, dict):
        return None

    max_tokens = request.get("max_tokens")
    if max_tokens is not None:
        try:
            if int(max_tokens) <= 1:
                return None  # probe request
        except (TypeError, ValueError):
            pass

    messages = request.get("messages")
    if not isinstance(messages, list):
        return None

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            texts = [
                b["text"]
                for b in content
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"]
            ]
            content = " ".join(texts)
        if not isinstance(content, str):
            continue
        text = _SYSTEM_REMINDER_RE.sub("", content).strip()
        if not text:
            continue
        text = " ".join(text.split())
        if len(text) > PREVIEW_MAX_LENGTH:
            text = text[:PREVIEW_MAX_LENGTH] + "..."
        return text

    return None


async def update_session_summary(
    conn: ConnectionProtocol,
    *,
    session_id: str,
    event_type: str,
    data: dict[str, Any],
    user_id: str | None,
    timestamp: datetime,
) -> None:
    """Upsert one event's contribution into ``session_summaries``.

    ``event_count`` increments per event. ``call_count`` increments once per
    call, keyed on the ``transaction.request_recorded`` event (the processor
    emits exactly one of those per call). ``models_used`` accumulates as a
    comma-joined list (a new model is appended only when not already present).
    ``preview_message`` is set once (the first non-probe user message wins) and
    never overwritten. ``user_id`` is filled the first time a non-null value is
    seen and never overwritten (COALESCE), matching ``conversation_calls``.

    Assumption: model names contain no comma. ``models_used`` is a single
    comma-delimited text column, so a comma inside a model name would corrupt
    both the dedupe membership test and any reader that splits on ``,``. Model
    names are Anthropic/provider model identifiers, which don't contain commas;
    if that ever changes this should move to a side table (see PR follow-ups).
    """
    is_request = event_type == "transaction.request_recorded"
    model = extract_model(data) if is_request else None
    preview = extract_preview(data) if is_request else None
    policy_inc = 1 if _is_policy_event(event_type) else 0
    call_inc = 1 if is_request else 0

    # New-model accumulation is a comma-joined set kept in a text column, dedup'd
    # inline. The membership test uses LIKE, so the model name must have LIKE
    # metacharacters escaped — otherwise a model containing '%' or '_' would
    # match unrelated entries and silently drop. We REPLACE-escape '\', '%', '_'
    # in the pattern operand only (not the value being stored) and declare
    # ESCAPE '\'. Both SQLite and Postgres support REPLACE and LIKE ... ESCAPE.
    await conn.execute(
        r"""
        INSERT INTO session_summaries (
            session_id, first_seen, last_seen, event_count, call_count,
            policy_event_count, user_id, models_used, preview_message
        )
        VALUES ($1, $2, $2, 1, $3, $4, $5, $6, $7)
        ON CONFLICT (session_id) DO UPDATE SET
            last_seen = CASE
                WHEN EXCLUDED.last_seen > session_summaries.last_seen
                THEN EXCLUDED.last_seen ELSE session_summaries.last_seen END,
            first_seen = CASE
                WHEN EXCLUDED.first_seen < session_summaries.first_seen
                THEN EXCLUDED.first_seen ELSE session_summaries.first_seen END,
            event_count = session_summaries.event_count + 1,
            call_count = session_summaries.call_count + $3,
            policy_event_count = session_summaries.policy_event_count + $4,
            user_id = COALESCE(session_summaries.user_id, EXCLUDED.user_id),
            models_used = CASE
                WHEN $6 IS NULL THEN session_summaries.models_used
                WHEN session_summaries.models_used IS NULL THEN $6
                WHEN ',' || session_summaries.models_used || ',' LIKE
                    '%,' || REPLACE(REPLACE(REPLACE($6, '\', '\\'), '%', '\%'), '_', '\_') || ',%'
                    ESCAPE '\'
                    THEN session_summaries.models_used
                ELSE session_summaries.models_used || ',' || $6 END,
            preview_message = COALESCE(session_summaries.preview_message, EXCLUDED.preview_message)
        """,
        session_id,
        timestamp,
        call_inc,
        policy_inc,
        user_id,
        model,
        preview,
    )


__all__ = [
    "PREVIEW_MAX_LENGTH",
    "extract_model",
    "extract_preview",
    "update_session_summary",
]
