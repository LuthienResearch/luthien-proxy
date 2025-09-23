"""HTTP routes for querying debug log data."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig

from .dependencies import get_database_pool, get_project_config

router = APIRouter()

logger = logging.getLogger(__name__)


class DebugEntry(BaseModel):
    """Row from debug_logs representing a single debug record."""

    id: str
    time_created: datetime
    debug_type_identifier: str
    jsonblob: dict[str, Any]


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
                jb = row["jsonblob"]
                if isinstance(jb, str):
                    try:
                        jb = json.loads(jb)
                    except Exception:
                        jb = {"raw": jb}
                entries.append(
                    DebugEntry(
                        id=str(row["id"]),
                        time_created=row["time_created"],
                        debug_type_identifier=row["debug_type_identifier"],
                        jsonblob=jb,
                    )
                )
    except Exception as exc:
        logger.error("Error fetching debug logs: %s", exc)
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
                        debug_type_identifier=row["debug_type_identifier"],
                        count=int(row["count"]),
                        latest=row["latest"],
                    )
                )
    except Exception as exc:
        logger.error("Error fetching debug types: %s", exc)
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
            total = int(total_row["cnt"]) if total_row else 0
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
                jb = row["jsonblob"]
                if isinstance(jb, str):
                    try:
                        jb = json.loads(jb)
                    except Exception:
                        jb = {"raw": jb}
                items.append(
                    DebugEntry(
                        id=str(row["id"]),
                        time_created=row["time_created"],
                        debug_type_identifier=row["debug_type_identifier"],
                        jsonblob=jb,
                    )
                )
    except Exception as exc:
        logger.error("Error fetching debug page: %s", exc)
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
