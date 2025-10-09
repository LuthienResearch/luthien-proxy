"""Database helpers for judge decision storage (now using policy_events)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Mapping

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db

logger = logging.getLogger(__name__)

JUDGE_DECISION_DEBUG_TYPE = "protection:llm-judge-block"


def _extract_timestamp(payload: Mapping[str, object]) -> datetime:
    raw_timestamp = payload.get("timestamp")
    if isinstance(raw_timestamp, datetime):
        return raw_timestamp
    if isinstance(raw_timestamp, str):
        try:
            parsed = datetime.fromisoformat(raw_timestamp)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            logger.debug("Failed to parse judge payload timestamp: %s", raw_timestamp)
    return datetime.now(UTC)


async def record_judge_decision(conn: db.ConnectionProtocol, payload: JSONObject) -> None:
    """Persist a judge decision payload using the policy_events table."""
    if not isinstance(payload, Mapping):
        logger.debug("Skipping judge payload with unexpected type: %s", type(payload).__name__)
        return

    call_id_raw = payload.get("call_id") or payload.get("litellm_call_id")
    if not isinstance(call_id_raw, str) or not call_id_raw:
        logger.debug("Skipping judge payload missing call_id")
        return
    call_id = call_id_raw

    # Extract judge-specific metadata
    metadata: dict[str, object] = {}

    tool_call = payload.get("tool_call")
    if isinstance(tool_call, Mapping):
        metadata["tool_call"] = tool_call

    probability_value = payload.get("probability")
    if isinstance(probability_value, (int, float)):
        metadata["probability"] = float(probability_value)

    explanation = payload.get("explanation")
    if explanation is not None:
        metadata["explanation"] = str(explanation)

    judge_response_text = payload.get("judge_response_text")
    if judge_response_text is not None:
        metadata["judge_response_text"] = str(judge_response_text)

    # Include other judge-specific data
    for key in ["judge_prompt", "original_request", "original_response", "stream_chunks", "blocked_response", "timing"]:
        if key in payload:
            metadata[key] = payload[key]

    judge_config = payload.get("judge_config")
    policy_config = judge_config if isinstance(judge_config, dict) else None

    created_at = _extract_timestamp(payload)

    # Get pool from connection (bit of a hack, but we have the connection already)
    # Actually, we need to create a temporary pool wrapper or use the connection directly
    # For now, let's just insert directly using the connection

    await conn.execute(
        """
        INSERT INTO policy_events (
            call_id,
            policy_class,
            policy_config,
            event_type,
            original_event_id,
            modified_event_id,
            metadata,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        call_id,
        "luthien_proxy.policies.tool_call_judge.ToolCallJudgePolicy",  # Hardcoded for now
        json.dumps(policy_config) if policy_config else None,
        "judge_decision",
        None,  # TODO: link to original event if we have it
        None,  # TODO: link to modified event if we have it
        json.dumps(metadata) if metadata else None,
        created_at,
    )


# Stub functions for backwards compatibility (TODO: migrate to policy_events queries)
async def load_judge_decisions(*args, **kwargs):
    """Stub - use load_policy_events instead."""
    return []


async def load_judge_traces(*args, **kwargs):
    """Stub - use policy_events queries instead."""
    return []


__all__ = [
    "record_judge_decision",
    "JUDGE_DECISION_DEBUG_TYPE",
    "load_judge_decisions",
    "load_judge_traces",
]
