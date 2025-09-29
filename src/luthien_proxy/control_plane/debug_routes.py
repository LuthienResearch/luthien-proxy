"""HTTP routes for querying debug log data."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig

from .dependencies import get_database_pool, get_project_config

router = APIRouter()

logger = logging.getLogger(__name__)


def _parse_debug_jsonblob(raw_blob: object) -> JSONObject:
    """Decode jsonblob column to a JSON object or return structured error payload."""
    if not isinstance(raw_blob, str):
        error = TypeError("debug_logs.jsonblob must be a JSON string")
        logger.error(f"Failed to parse debug_logs.jsonblob: {error}")
        return cast(JSONObject, {"raw": raw_blob, "error": str(error)})
    try:
        parsed_blob = json.loads(raw_blob)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse debug_logs.jsonblob: {exc}")
        return cast(JSONObject, {"raw": raw_blob, "error": str(exc)})
    if not isinstance(parsed_blob, dict):
        error = TypeError("debug_logs.jsonblob must decode to a JSON object")
        logger.error(f"Failed to parse debug_logs.jsonblob: {error}")
        return cast(JSONObject, {"raw": raw_blob, "error": str(error)})
    return cast(JSONObject, parsed_blob)


def _require_str(value: object, context: str) -> str:
    """Return *value* when it is a non-empty string."""
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"{context} must be a non-empty string; saw {type(value)!r}")


def _require_datetime(value: object, context: str) -> datetime:
    """Return *value* when it is a datetime."""
    if isinstance(value, datetime):
        return value
    raise ValueError(f"{context} must be a datetime; saw {type(value)!r}")


def _require_int(value: object, context: str) -> int:
    """Return *value* as an int when numeric."""
    if isinstance(value, bool):
        raise ValueError(f"{context} must be an int; saw bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    raise ValueError(f"{context} must be numeric; saw {type(value)!r}")


class DebugEntry(BaseModel):
    """Row from debug_logs representing a single debug record."""

    id: str
    time_created: datetime
    debug_type_identifier: str
    jsonblob: JSONObject


class DebugTypeInfo(BaseModel):
    """Aggregated counts and latest timestamp per debug type."""

    debug_type_identifier: str
    count: int
    latest: datetime


class DebugPage(BaseModel):
    """A simple paginated list of debug entries."""

    items: list[DebugEntry]
    page: int
    page_size: int
    total: int


@router.get("/api/debug/{debug_type}", response_model=list[DebugEntry])
async def get_debug_entries(
    debug_type: str,
    limit: int = Query(default=50, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[DebugEntry]:
    """Return latest debug entries for a given type (paged by limit)."""
    entries: list[DebugEntry] = []
    if config.database_url is None or pool is None:
        return entries
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE debug_type_identifier = $1
                ORDER BY time_created DESC
                LIMIT $2
                """,
                debug_type,
                limit,
            )
            for row in rows:
                jb = _parse_debug_jsonblob(row["jsonblob"])
                entries.append(
                    DebugEntry(
                        id=str(row["id"]),
                        time_created=_require_datetime(row.get("time_created"), "time_created"),
                        debug_type_identifier=_require_str(row.get("debug_type_identifier"), "debug_type_identifier"),
                        jsonblob=jb,
                    )
                )
    except Exception as exc:
        logger.error(f"Error fetching debug logs: {exc}")
    return entries


@router.get("/api/debug/types", response_model=list[DebugTypeInfo])
async def get_debug_types(
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[DebugTypeInfo]:
    """Return summary of available debug types with counts."""
    types: list[DebugTypeInfo] = []
    if config.database_url is None or pool is None:
        return types
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT debug_type_identifier, COUNT(*) as count, MAX(time_created) as latest
                FROM debug_logs
                GROUP BY debug_type_identifier
                ORDER BY latest DESC
                """
            )
            for row in rows:
                types.append(
                    DebugTypeInfo(
                        debug_type_identifier=_require_str(row.get("debug_type_identifier"), "debug_type_identifier"),
                        count=_require_int(row.get("count"), "count"),
                        latest=_require_datetime(row.get("latest"), "latest"),
                    )
                )
    except Exception as exc:
        logger.error(f"Error fetching debug types: {exc}")
    return types


@router.get("/api/debug/{debug_type}/page", response_model=DebugPage)
async def get_debug_page(
    debug_type: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> DebugPage:
    """Return a paginated slice of debug entries for a type."""
    items: list[DebugEntry] = []
    total = 0
    if config.database_url is None or pool is None:
        return DebugPage(items=items, page=page, page_size=page_size, total=total)
    try:
        async with pool.connection() as conn:
            total_row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt FROM debug_logs WHERE debug_type_identifier = $1
                """,
                debug_type,
            )
            total = _require_int(total_row["cnt"], "cnt") if total_row else 0
            offset = (page - 1) * page_size
            rows = await conn.fetch(
                """
                SELECT id, time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE debug_type_identifier = $1
                ORDER BY time_created DESC
                LIMIT $2 OFFSET $3
                """,
                debug_type,
                page_size,
                offset,
            )
            for row in rows:
                jb = _parse_debug_jsonblob(row["jsonblob"])
                items.append(
                    DebugEntry(
                        id=str(row["id"]),
                        time_created=_require_datetime(row.get("time_created"), "time_created"),
                        debug_type_identifier=_require_str(row.get("debug_type_identifier"), "debug_type_identifier"),
                        jsonblob=jb,
                    )
                )
    except Exception as exc:
        logger.error(f"Error fetching debug page: {exc}")
    return DebugPage(items=items, page=page, page_size=page_size, total=total)


__all__ = [
    "router",
    "DebugEntry",
    "DebugTypeInfo",
    "DebugPage",
    "get_debug_entries",
    "get_debug_types",
    "get_debug_page",
]
