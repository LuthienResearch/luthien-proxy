"""Query service for request/response logs.

Pure business logic — no FastAPI dependencies. Functions accept a
DatabasePool and return Pydantic models.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from luthien_proxy.request_log.models import (
    RequestLogDetailResponse,
    RequestLogEntry,
    RequestLogListResponse,
)

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


def _parse_jsonb(raw: object) -> dict[str, Any] | None:
    """Parse a JSONB column that asyncpg may return as dict or str."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return json.loads(raw)  # type: ignore[no-any-return]
    return None


def _row_to_entry(row: Any) -> RequestLogEntry:
    """Convert a database row to a RequestLogEntry."""
    return RequestLogEntry(
        id=str(row["id"]),
        transaction_id=str(row["transaction_id"]),
        session_id=str(row["session_id"]) if row["session_id"] else None,
        direction=str(row["direction"]),
        http_method=str(row["http_method"]) if row["http_method"] else None,
        url=str(row["url"]) if row["url"] else None,
        request_headers=_parse_jsonb(row["request_headers"]),
        request_body=_parse_jsonb(row["request_body"]),
        response_status=int(row["response_status"]) if row["response_status"] is not None else None,
        response_headers=_parse_jsonb(row["response_headers"]),
        response_body=_parse_jsonb(row["response_body"]),
        started_at=cast(datetime, row["started_at"]).isoformat(),
        completed_at=cast(datetime, row["completed_at"]).isoformat() if row["completed_at"] else None,
        duration_ms=float(row["duration_ms"]) if row["duration_ms"] is not None else None,
        model=str(row["model"]) if row["model"] else None,
        is_streaming=bool(row["is_streaming"]),
        endpoint=str(row["endpoint"]) if row["endpoint"] else None,
    )


async def list_request_logs(
    db_pool: DatabasePool,
    *,
    limit: int = 50,
    offset: int = 0,
    direction: str | None = None,
    endpoint: str | None = None,
    session_id: str | None = None,
    status: int | None = None,
    model: str | None = None,
    after: str | None = None,
    before: str | None = None,
    search: str | None = None,
) -> RequestLogListResponse:
    """List request logs with optional filters.

    Args:
        db_pool: Database connection pool.
        limit: Max entries to return (capped at 200).
        offset: Pagination offset.
        direction: Filter by 'inbound' or 'outbound'.
        endpoint: Filter by endpoint path.
        session_id: Filter by session ID.
        status: Filter by response status code.
        model: Filter by model name.
        after: ISO datetime — only entries started at or after this time.
        before: ISO datetime — only entries started before this time.
        search: Substring search in request/response body (uses JSONB cast).

    Returns:
        Paginated list of log entries.
    """
    limit = min(limit, 200)
    conditions: list[str] = []
    params: list[Any] = []
    param_idx = 0

    def _add(clause: str, value: Any) -> None:
        nonlocal param_idx
        param_idx += 1
        conditions.append(clause.replace("?", f"${param_idx}"))
        params.append(value)

    if direction:
        _add("direction = ?", direction)
    if endpoint:
        _add("endpoint = ?", endpoint)
    if session_id:
        _add("session_id = ?", session_id)
    if status is not None:
        _add("response_status = ?", status)
    if model:
        _add("model = ?", model)
    if after:
        _add("started_at >= ?::timestamptz", after)
    if before:
        _add("started_at < ?::timestamptz", before)
    if search:
        _add(
            "(request_body::text ILIKE '%' || ? || '%' OR response_body::text ILIKE '%' || ? || '%')",
            search,
        )
        # The search param is used twice in the clause, so add it again
        param_idx += 1
        params.append(search)
        # Fix: rewrite the clause to use proper param indices
        conditions[-1] = (
            f"(request_body::text ILIKE '%' || ${param_idx - 1} || '%'"
            f" OR response_body::text ILIKE '%' || ${param_idx} || '%')"
        )

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with db_pool.connection() as conn:
        # Count total matching rows
        count_row = await conn.fetchrow(f"SELECT COUNT(*) as cnt FROM request_logs {where}", *params)
        total = int(count_row["cnt"]) if count_row else 0  # type: ignore[arg-type]

        # Fetch page
        param_idx += 1
        limit_param = f"${param_idx}"
        param_idx += 1
        offset_param = f"${param_idx}"

        rows = await conn.fetch(
            f"""
            SELECT * FROM request_logs
            {where}
            ORDER BY started_at DESC
            LIMIT {limit_param} OFFSET {offset_param}
            """,
            *params,
            limit,
            offset,
        )

    return RequestLogListResponse(
        logs=[_row_to_entry(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_transaction_logs(
    db_pool: DatabasePool,
    transaction_id: str,
) -> RequestLogDetailResponse:
    """Get all log entries for a single transaction.

    Args:
        db_pool: Database connection pool.
        transaction_id: The transaction_id linking inbound + outbound.

    Returns:
        Detail response with inbound and outbound entries.

    Raises:
        ValueError: If no logs found for the transaction_id.
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM request_logs WHERE transaction_id = $1 ORDER BY direction",
            transaction_id,
        )

    if not rows:
        raise ValueError(f"No request logs found for transaction_id: {transaction_id}")

    inbound = None
    outbound = None
    session_id = None

    for row in rows:
        entry = _row_to_entry(row)
        if entry.direction == "inbound":
            inbound = entry
        elif entry.direction == "outbound":
            outbound = entry
        if entry.session_id:
            session_id = entry.session_id

    return RequestLogDetailResponse(
        transaction_id=transaction_id,
        session_id=session_id,
        inbound=inbound,
        outbound=outbound,
    )


__all__ = ["list_request_logs", "get_transaction_logs"]
