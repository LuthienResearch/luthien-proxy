"""Utility helpers for persisting debug log records."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from luthien_proxy.utils import db

logger = logging.getLogger(__name__)


async def insert_debug(
    pool: Optional[db.DatabasePool],
    debug_type: str,
    payload: dict[str, Any],
) -> None:
    """Insert a debug log row into the database (best-effort)."""
    if pool is None:
        return
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
    except Exception as exc:  # pragma: no cover - avoid masking hook flow
        logger.error("Error inserting debug log: %s", exc)


__all__ = ["insert_debug"]
