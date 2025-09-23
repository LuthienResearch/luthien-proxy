"""HTTP routes for receiving LiteLLM hook callbacks and trace queries."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from luthien_proxy.control_plane.utils.hooks import extract_call_id_for_hook
from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig

from .dependencies import (
    DebugLogWriter,
    get_active_policy,
    get_database_pool,
    get_debug_log_writer,
    get_hook_counter_state,
    get_project_config,
)

router = APIRouter()

logger = logging.getLogger(__name__)


class TraceEntry(BaseModel):
    """A single hook event for a call ID, optionally with nanosecond time."""

    time: datetime
    post_time_ns: Optional[int] = None
    hook: Optional[str] = None
    debug_type: Optional[str] = None
    payload: dict[str, Any]


class TraceResponse(BaseModel):
    """Ordered list of hook entries belonging to a call ID."""

    call_id: str
    entries: list[TraceEntry]


class CallIdInfo(BaseModel):
    """Summary row for a recent litellm_call_id with counts and latest time."""

    call_id: str
    count: int
    latest: datetime


@router.get("/api/hooks/counters")
async def get_hook_counters(
    counters: Counter[str] = Depends(get_hook_counter_state),
) -> dict[str, int]:
    """Expose in-memory hook counters for sanity/testing scripts."""
    return dict(counters)


@router.post("/hooks/{hook_name}")
async def hook_generic(
    hook_name: str,
    payload: dict[str, Any],
    debug_writer: DebugLogWriter = Depends(get_debug_log_writer),
    policy: LuthienPolicy = Depends(get_active_policy),
    counters: Counter[str] = Depends(get_hook_counter_state),
) -> Any:
    """Generic hook endpoint for any CustomLogger hook."""
    try:
        record = {
            "hook": hook_name,
            "payload": payload,
        }
        logger.debug("hook=%s payload=%s", hook_name, json.dumps(payload, ensure_ascii=False))
        try:
            call_id = extract_call_id_for_hook(hook_name, payload)
            if isinstance(call_id, str) and call_id:
                record["litellm_call_id"] = call_id
        except Exception:
            pass
        asyncio.create_task(debug_writer(f"hook:{hook_name}", record))
        name = hook_name.lower()
        counters[name] += 1
        handler = cast(
            Optional[Callable[..., Awaitable[Any]]],
            getattr(policy, name, None),
        )
        payload.pop("post_time_ns", None)
        if handler:
            return await handler(**payload)
        return payload
    except Exception as exc:
        logger.error("hook_generic_error: %s", exc)
        raise HTTPException(status_code=500, detail=f"hook_generic_error: {exc}")


def _parse_jsonblob(raw: Any) -> dict[str, Any]:
    """Return a dict for a row's jsonblob without raising."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": raw}
        except Exception:
            return {"raw": raw}
    return {"raw": raw}


def _extract_post_ns(jb: dict[str, Any]) -> Optional[int]:
    payload = jb.get("payload")
    if not isinstance(payload, dict):
        return None
    ns = payload.get("post_time_ns")
    if isinstance(ns, int):
        return ns
    if isinstance(ns, float):
        return int(ns)
    return None


@router.get("/api/hooks/trace_by_call_id", response_model=TraceResponse)
async def trace_by_call_id(
    call_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceResponse:
    """Return ordered hook entries from debug_logs for a litellm_call_id."""
    entries: list[TraceEntry] = []
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for trace lookups")
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE jsonblob->>'litellm_call_id' = $1
                ORDER BY time_created ASC
                """,
                call_id,
            )
            for row in rows:
                jb = _parse_jsonblob(row["jsonblob"])
                entries.append(
                    TraceEntry(
                        time=row["time_created"],
                        post_time_ns=_extract_post_ns(jb),
                        hook=jb.get("hook"),
                        debug_type=row["debug_type_identifier"],
                        payload=jb,
                    )
                )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace_error: {exc}")

    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return TraceResponse(call_id=call_id, entries=entries)


@router.get("/api/hooks/recent_call_ids", response_model=list[CallIdInfo])
async def recent_call_ids(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[CallIdInfo]:
    """Return recent call IDs observed in debug logs with usage counts."""
    out: list[CallIdInfo] = []
    if config.database_url is None or pool is None:
        return out
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT jsonblob->>'litellm_call_id' as cid,
                       COUNT(*) as cnt,
                       MAX(time_created) as latest
                FROM debug_logs
                WHERE jsonblob->>'litellm_call_id' IS NOT NULL
                GROUP BY cid
                ORDER BY latest DESC
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                cid = row["cid"]
                if not cid:
                    continue
                out.append(CallIdInfo(call_id=cid, count=int(row["cnt"]), latest=row["latest"]))
    except Exception as exc:
        logger.error("Error fetching recent call ids: %s", exc)
    return out


__all__ = [
    "router",
    "get_hook_counters",
    "TraceEntry",
    "TraceResponse",
    "CallIdInfo",
    "hook_generic",
    "trace_by_call_id",
    "recent_call_ids",
]
