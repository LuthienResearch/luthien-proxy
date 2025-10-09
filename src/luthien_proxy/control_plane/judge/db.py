"""Database helpers for judge decision storage and retrieval."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Mapping, Optional, Sequence

from fastapi import HTTPException

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig
from luthien_proxy.utils.validation import require_type

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


def _to_json(value: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    return None


async def record_judge_decision(conn: db.ConnectionProtocol, payload: JSONObject) -> None:
    """Persist a judge decision payload into structured storage."""
    if not isinstance(payload, Mapping):
        logger.debug("Skipping judge payload with unexpected type: %s", type(payload).__name__)
        return

    call_id_raw = payload.get("call_id") or payload.get("litellm_call_id")
    if not isinstance(call_id_raw, str) or not call_id_raw:
        logger.debug("Skipping judge payload missing call_id")
        return
    call_id = call_id_raw

    trace_id = payload.get("trace_id")
    if not isinstance(trace_id, str):
        trace_id = None

    tool_call = payload.get("tool_call") if isinstance(payload.get("tool_call"), Mapping) else None
    tool_call_id: Optional[str] = None
    if isinstance(tool_call, Mapping):
        tool_call_id_raw = tool_call.get("id")
        if isinstance(tool_call_id_raw, str) and tool_call_id_raw:
            tool_call_id = tool_call_id_raw

    probability_value = payload.get("probability")
    probability = float(probability_value) if isinstance(probability_value, (int, float)) else None

    explanation_raw = payload.get("explanation")
    explanation = str(explanation_raw) if explanation_raw is not None else None

    judge_response_raw = payload.get("judge_response_text")
    judge_response_text = str(judge_response_raw) if judge_response_raw is not None else None

    created_at = _extract_timestamp(payload)

    await conn.execute(
        """
        INSERT INTO conversation_judge_decisions (
            call_id,
            trace_id,
            tool_call_id,
            probability,
            explanation,
            tool_call,
            judge_prompt,
            judge_response_text,
            original_request,
            original_response,
            stream_chunks,
            blocked_response,
            timing,
            judge_config,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
        call_id,
        trace_id,
        tool_call_id,
        probability,
        explanation,
        _to_json(tool_call),
        _to_json(payload.get("judge_prompt")),
        judge_response_text,
        _to_json(payload.get("original_request")),
        _to_json(payload.get("original_response")),
        _to_json(payload.get("stream_chunks")),
        _to_json(payload.get("blocked_response")),
        _to_json(payload.get("timing")),
        _to_json(payload.get("judge_config")),
        created_at,
    )


async def load_judge_decisions(
    *,
    trace_id: str,
    call_id: str | None,
    limit: int,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[dict[str, object]]:
    """Fetch judge decisions for a trace with optional call filter."""
    if config.database_url is None or pool is None:
        return []

    conditions = ["trace_id = $1"]
    params: list[object] = [trace_id]

    if call_id:
        conditions.append(f"call_id = ${len(params) + 1}")
        params.append(call_id)

    params.append(limit)

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT call_id,
               trace_id,
               tool_call_id,
               probability,
               explanation,
               tool_call,
               judge_prompt,
               judge_response_text,
               original_request,
               original_response,
               stream_chunks,
               blocked_response,
               timing,
               judge_config,
               created_at
        FROM conversation_judge_decisions
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """

    rows: Sequence[Mapping[str, object]]
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(query, *params)
    except Exception as exc:
        logger.error("Failed to load judge decisions: %s", exc)
        raise HTTPException(status_code=500, detail=f"judge_decisions_error: {exc}")

    records: list[dict[str, object]] = []
    for row in rows:
        timestamp = require_type(row.get("created_at"), datetime, "created_at")
        record: dict[str, object] = {
            "call_id": require_type(row.get("call_id"), str, "call_id"),
            "trace_id": row.get("trace_id"),
            "timestamp": timestamp,
            "probability": row.get("probability"),
            "explanation": row.get("explanation"),
            "tool_call": row.get("tool_call"),
            "judge_prompt": row.get("judge_prompt"),
            "judge_response_text": row.get("judge_response_text"),
            "original_request": row.get("original_request"),
            "original_response": row.get("original_response"),
            "stream_chunks": row.get("stream_chunks"),
            "blocked_response": row.get("blocked_response"),
            "timing": row.get("timing"),
            "judge_config": row.get("judge_config"),
        }
        records.append(record)
    return records


async def load_judge_traces(
    *,
    limit: int,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[dict[str, object]]:
    """Aggregate judge decisions by trace for dashboard listings."""
    if config.database_url is None or pool is None:
        return []

    query = """
        SELECT trace_id,
               MIN(created_at) AS first_seen,
               MAX(created_at) AS last_seen,
               COUNT(*) AS block_count,
               MAX(COALESCE(probability, 0)) AS max_probability
        FROM conversation_judge_decisions
        WHERE trace_id IS NOT NULL
        GROUP BY trace_id
        ORDER BY last_seen DESC
        LIMIT $1
        """

    rows: Sequence[Mapping[str, object]]
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(query, limit)
    except Exception as exc:
        logger.error("Failed to load judge trace summaries: %s", exc)
        raise HTTPException(status_code=500, detail=f"judge_trace_error: {exc}")

    summaries: list[dict[str, object]] = []
    for row in rows:
        trace_id = row.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            continue
        summaries.append(
            {
                "trace_id": trace_id,
                "first_seen": require_type(row.get("first_seen"), datetime, "first_seen"),
                "last_seen": require_type(row.get("last_seen"), datetime, "last_seen"),
                "block_count": require_type(row.get("block_count"), int, "block_count"),
                "max_probability": float(row.get("max_probability") or 0.0),
            }
        )
    return summaries


__all__ = [
    "load_judge_decisions",
    "load_judge_traces",
    "record_judge_decision",
    "JUDGE_DECISION_DEBUG_TYPE",
]
