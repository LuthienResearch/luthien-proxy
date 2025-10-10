"""Helpers for recording debug events in the database."""

from __future__ import annotations

import json
import logging

from luthien_proxy.control_plane.judge import JUDGE_DECISION_DEBUG_TYPE, record_judge_decision
from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db

logger = logging.getLogger(__name__)


async def record_debug_event(
    pool: db.DatabasePool,
    debug_type: str,
    payload: JSONObject,
) -> None:
    """Persist a debug entry for later inspection (best-effort)."""
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO debug_logs (debug_type_identifier, jsonblob)
                VALUES ($1, $2)
                """,
                debug_type,
                json.dumps(payload),
            )
            if debug_type == JUDGE_DECISION_DEBUG_TYPE:
                try:
                    await record_judge_decision(conn, payload)
                except Exception as judge_exc:  # pragma: no cover - keep debug logging resilient
                    logger.error("Error recording judge decision: %s", judge_exc)
    except Exception as exc:  # pragma: no cover - avoid masking hook flow
        logger.error("Error inserting debug log: %s", exc)


__all__ = ["record_debug_event"]
