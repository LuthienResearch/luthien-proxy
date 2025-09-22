"""FastAPI app for the Luthien Control Plane.

Provides endpoints that receive LiteLLM hook events, lightweight debug UIs,
and small helper APIs. Policy decisions and persistence stay outside this
module to keep the web layer thin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from functools import partial
from typing import Any, Awaitable, Callable, Coroutine, Optional, cast

import yaml
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from luthien_proxy.control_plane.stream_context import StreamContextStore
from luthien_proxy.control_plane.ui import router as ui_router
from luthien_proxy.control_plane.utils.hooks import extract_call_id_for_hook
from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.policies.engine import PolicyEngine
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.utils import db
from luthien_proxy.utils.project_config import ProjectConfig

DebugLogWriter = Callable[[str, dict[str, Any], Optional[db.ConnectFn]], Coroutine[Any, Any, None]]

router = APIRouter()


_logger = logging.getLogger("luthien.control_plane.hooks")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_logger.addHandler(handler)
_logger.setLevel(logging.INFO)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


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


def get_project_config(request: Request) -> ProjectConfig:
    """Return the ProjectConfig stored on app.state."""
    config = getattr(request.app.state, "project_config", None)
    if config is None:
        raise RuntimeError("ProjectConfig is not configured for this app instance")
    return cast(ProjectConfig, config)


def get_active_policy(request: Request) -> LuthienPolicy:
    """Return the active policy from app.state."""
    policy = getattr(request.app.state, "active_policy", None)
    if policy is None:
        raise RuntimeError("Active policy not loaded for this app instance")
    return cast(LuthienPolicy, policy)


def get_hook_counter_state(request: Request) -> Counter[str]:
    """Return the in-memory hook counters for this app."""
    counters = getattr(request.app.state, "hook_counters", None)
    if counters is None:
        raise RuntimeError("Hook counters not initialized")
    return cast(Counter[str], counters)


def get_debug_log_writer(request: Request) -> DebugLogWriter:
    """Return the async debug log writer stored on app.state."""
    writer = getattr(request.app.state, "debug_log_writer", None)
    if writer is None:
        raise RuntimeError("Debug log writer not configured")
    return cast(DebugLogWriter, writer)


async def _insert_debug(
    config: ProjectConfig,
    debug_type: str,
    payload: dict[str, Any],
    connect: db.ConnectFn | None = None,
) -> None:
    """Insert a debug log row into the database (best-effort)."""
    database_url = config.database_url
    if database_url is None:
        return
    try:
        conn = await db.open_connection(connect, url=database_url)
        try:
            await conn.execute(
                """
                INSERT INTO debug_logs (debug_type_identifier, jsonblob)
                VALUES ($1, $2)
                """,
                debug_type,
                json.dumps(payload),
            )
        finally:
            await db.close_connection(conn)
    except Exception as exc:  # pragma: no cover - avoid masking hook flow
        print(f"Error inserting debug log: {exc}")


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Return a simple health payload without touching external services."""
    return {"status": "healthy", "service": "luthien-control-plane", "version": "0.1.0"}


@router.get("/endpoints")
async def list_endpoints() -> dict[str, Any]:
    """List notable HTTP endpoints for quick discoverability."""
    return {
        "hooks": [
            "POST /hooks/{hook_name}",
        ],
        "health": "GET /health",
    }


@router.get("/api/debug/{debug_type}", response_model=list[DebugEntry])
async def get_debug_entries(
    debug_type: str,
    limit: int = Query(default=50, le=500),
    connect: db.ConnectFn = Depends(db.get_connector),
    config: ProjectConfig = Depends(get_project_config),
) -> list[DebugEntry]:
    """Return latest debug entries for a given type (paged by limit)."""
    entries: list[DebugEntry] = []
    if config.database_url is None:
        return entries
    try:
        conn = await db.open_connection(connect, url=config.database_url)
        try:
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
        finally:
            await db.close_connection(conn)
    except Exception as exc:
        print(f"Error fetching debug logs: {exc}")
    return entries


@router.get("/api/debug/types", response_model=list[DebugTypeInfo])
async def get_debug_types(
    connect: db.ConnectFn = Depends(db.get_connector),
    config: ProjectConfig = Depends(get_project_config),
) -> list[DebugTypeInfo]:
    """Return summary of available debug types with counts."""
    types: list[DebugTypeInfo] = []
    if config.database_url is None:
        return types
    try:
        conn = await db.open_connection(connect, url=config.database_url)
        try:
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
        finally:
            await db.close_connection(conn)
    except Exception as exc:
        print(f"Error fetching debug types: {exc}")
    return types


@router.get("/api/debug/{debug_type}/page", response_model=DebugPage)
async def get_debug_page(
    debug_type: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    connect: db.ConnectFn = Depends(db.get_connector),
    config: ProjectConfig = Depends(get_project_config),
) -> DebugPage:
    """Return a paginated slice of debug entries for a type."""
    items: list[DebugEntry] = []
    total = 0
    if config.database_url is None:
        return DebugPage(items=items, page=page, page_size=page_size, total=total)
    try:
        conn = await db.open_connection(connect, url=config.database_url)
        try:
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
        finally:
            await db.close_connection(conn)
    except Exception as exc:
        print(f"Error fetching debug page: {exc}")
    return DebugPage(items=items, page=page, page_size=page_size, total=total)


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
        _logger.debug("hook=%s payload=%s", hook_name, json.dumps(payload, ensure_ascii=False))
        try:
            call_id = extract_call_id_for_hook(hook_name, payload)
            if isinstance(call_id, str) and call_id:
                record["litellm_call_id"] = call_id
        except Exception:
            pass
        asyncio.create_task(debug_writer(f"hook:{hook_name}", record, None))
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
        _logger.error(f"hook_generic_error: {exc}")
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
    connect: db.ConnectFn = Depends(db.get_connector),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceResponse:
    """Return ordered hook entries from debug_logs for a litellm_call_id."""
    entries: list[TraceEntry] = []
    if config.database_url is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for trace lookups")
    try:
        conn = await db.open_connection(connect, url=config.database_url)
        try:
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
        finally:
            await db.close_connection(conn)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace_error: {exc}")

    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return TraceResponse(call_id=call_id, entries=entries)


@router.get("/api/hooks/recent_call_ids", response_model=list[CallIdInfo])
async def recent_call_ids(
    limit: int = Query(default=50, ge=1, le=500),
    connect: db.ConnectFn = Depends(db.get_connector),
    config: ProjectConfig = Depends(get_project_config),
) -> list[CallIdInfo]:
    """Return recent call IDs observed in debug logs with usage counts."""
    out: list[CallIdInfo] = []
    if config.database_url is None:
        return out
    try:
        conn = await db.open_connection(connect, url=config.database_url)
        try:
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
        finally:
            await db.close_connection(conn)
    except Exception as exc:
        print(f"Error fetching recent call ids: {exc}")
    return out


def _load_policy_from_config(
    config: ProjectConfig,
    config_path: Optional[str] = None,
) -> LuthienPolicy:
    """Load the active policy from YAML config or return `NoOpPolicy`."""
    resolved_path = config_path or config.luthien_policy_config
    if not resolved_path:
        raise RuntimeError("LUTHIEN_POLICY_CONFIG must be set to load a policy")

    def _read(path: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        if not os.path.exists(path):
            print(f"Policy config not found at {path}; using NoOpPolicy")
            return None, None
        try:
            with open(path, "r", encoding="utf-8") as file:
                cfg = yaml.safe_load(file) or {}
            return cfg.get("policy"), (cfg.get("policy_options") or None)
        except Exception as exc:
            print(f"Failed to read policy config {path}: {exc}")
            return None, None

    def _import(ref: str):
        try:
            module_path, class_name = ref.split(":", 1)
            module = __import__(module_path, fromlist=[class_name])
            cls = getattr(module, class_name)
            return cls, module_path, class_name
        except Exception as exc:
            print(f"Failed to import policy '{ref}': {exc}")
            return None, None, None

    def _instantiate(cls, options: Optional[dict[str, Any]]) -> LuthienPolicy:
        if options is not None:
            try:
                return cast(Any, cls)(options=options)
            except TypeError:
                pass
        return cls()

    policy_ref, policy_options = _read(resolved_path)
    if not policy_ref:
        print("No policy specified in config; using NoOpPolicy")
        return NoOpPolicy()

    cls, module_path, class_name = _import(policy_ref)
    if not cls or not module_path or not class_name:
        return NoOpPolicy()
    if not issubclass(cls, LuthienPolicy):
        print(f"Configured policy {class_name} does not subclass LuthienPolicy; using NoOpPolicy")
        return NoOpPolicy()

    print(f"Loaded policy from config: {class_name} ({module_path})")
    return _instantiate(cls, policy_options)


def create_control_plane_app(config: ProjectConfig) -> FastAPI:
    """Construct a FastAPI app instance configured with the provided project config."""
    hook_counters: Counter[str] = Counter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.project_config = config
        app.state.hook_counters = hook_counters
        app.state.debug_log_writer = partial(_insert_debug, config)

        control_cfg = config.control_plane_config
        engine = PolicyEngine(
            database_url=config.database_url or "",
            redis_url=control_cfg.redis_url,
        )
        await engine.initialize()

        policy = _load_policy_from_config(config, control_cfg.policy_config_path)

        if not engine.redis_client:
            raise RuntimeError("Redis client unavailable; ensure REDIS_URL is correct")

        stream_store = StreamContextStore(
            redis_client=engine.redis_client,
            ttl_seconds=control_cfg.stream_context_ttl,
        )

        app.state.policy_engine = engine
        app.state.active_policy = policy
        app.state.stream_store = stream_store

        print("Control plane services initialized successfully")
        try:
            yield
        finally:
            app.state.policy_engine = None
            app.state.active_policy = None
            app.state.stream_store = None

    app = FastAPI(
        title="Luthien Control Plane",
        description="AI Control policy orchestration service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(ui_router)
    app.include_router(router)

    return app


__all__ = [
    "create_control_plane_app",
    "get_project_config",
    "get_hook_counters",
    "list_endpoints",
    "health_check",
    "trace_by_call_id",
    "recent_call_ids",
    "get_debug_entries",
    "get_debug_types",
    "get_debug_page",
    "hook_generic",
    "_load_policy_from_config",
]
