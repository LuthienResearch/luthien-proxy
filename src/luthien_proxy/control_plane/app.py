"""FastAPI app for the Luthien Control Plane.

Provides endpoints that receive LiteLLM hook events, lightweight debug UIs,
and small helper APIs. Policy decisions and persistence stay outside this
module to keep the web layer thin.
"""

import asyncio
import json
import logging
import os
import sys
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, cast

import asyncpg
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from luthien_proxy.control_plane.stream_context import StreamContextStore
from luthien_proxy.control_plane.ui import router as ui_router
from luthien_proxy.control_plane.utils.hooks import (
    extract_call_id_for_hook,
)
from luthien_proxy.policies.base import LuthienPolicy

# Import our policy and monitoring modules (will implement these)
from luthien_proxy.policies.engine import PolicyEngine
from luthien_proxy.policies.noop import NoOpPolicy


# FastAPI app setup
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and wire core services during app startup."""
    global policy_engine, active_policy, stream_store
    try:
        if os.getenv("DATABASE_URL") or os.getenv("REDIS_URL"):
            policy_engine = PolicyEngine(
                database_url=os.getenv("DATABASE_URL"),
                redis_url=os.getenv("REDIS_URL"),
            )
            await policy_engine.initialize()

        active_policy = _load_policy_from_config()

        if not policy_engine or not policy_engine.redis_client:
            raise RuntimeError("Redis is required for streaming context; set REDIS_URL and ensure connectivity")
        stream_store = StreamContextStore(
            redis_client=policy_engine.redis_client,
            ttl_seconds=int(os.getenv("STREAM_CONTEXT_TTL", "3600")),
        )
        print("Control plane services initialized successfully")
        yield
    except Exception as e:
        print(f"Error initializing control plane: {e}")
        raise


app = FastAPI(
    title="Luthien Control Plane",
    description="AI Control policy orchestration service",
    version="0.1.0",
    lifespan=lifespan,
)

# Simple stdout logger for hook payload visibility in Docker logs
_logger = logging.getLogger("luthien.control_plane.hooks")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_logger.addHandler(handler)
_logger.setLevel(logging.INFO)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static assets (JS/CSS) for debug and logs views
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Include UI routes (templates)
app.include_router(ui_router)

# Global instances (will be initialized on startup)
policy_engine: Optional[PolicyEngine] = None
active_policy: Optional[LuthienPolicy] = None
stream_store: Optional[StreamContextStore] = None
_hook_counters = Counter()
_hook_logs: deque = deque(maxlen=500)


async def _insert_debug(debug_type: str, payload: dict[str, Any]) -> None:
    """Insert a debug log row into the database (best-effort)."""
    db_url = os.getenv("DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien")
    try:
        conn = await asyncpg.connect(db_url)
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
            await conn.close()
    except Exception as e:
        # Log error to stdout; do not raise to avoid breaking hooks
        print(f"Error inserting debug log: {e}")

    # startup handled by lifespan above


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Return a simple health payload without touching external services."""
    return {"status": "healthy", "service": "luthien-control-plane", "version": "0.1.0"}


def _load_policy_from_config() -> LuthienPolicy:
    """Load the active policy from YAML config or return `NoOpPolicy`.

    Why: Keep this thin and predictable. Break work into three simple pieces:
    read config → import class → instantiate with optional options.
    """
    config_path = os.getenv("LUTHIEN_POLICY_CONFIG", "/app/config/luthien_config.yaml")

    def _read(path: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        if not os.path.exists(path):
            print(f"Policy config not found at {path}; using NoOpPolicy")
            return None, None
        try:
            with open(path, "r") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("policy"), (cfg.get("policy_options") or None)
        except Exception as e:
            print(f"Failed to read policy config {path}: {e}")
            return None, None

    def _import(ref: str):
        try:
            module_path, class_name = ref.split(":", 1)
            module = __import__(module_path, fromlist=[class_name])
            cls = getattr(module, class_name)
            return cls, module_path, class_name
        except Exception as e:
            print(f"Failed to import policy '{ref}': {e}")
            return None, None, None

    def _instantiate(cls, options: Optional[dict[str, Any]]) -> LuthienPolicy:
        # Prefer explicit options via constructor if supported
        if options is not None:
            try:
                return cast(Any, cls)(options=options)
            except TypeError:
                # Back-compat: some policies read options from env
                try:
                    os.environ["LUTHIEN_POLICY_OPTIONS_JSON"] = json.dumps(options)
                except Exception:
                    pass
        return cls()

    policy_ref, policy_options = _read(config_path)
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


@app.get("/endpoints")
async def list_endpoints() -> dict[str, Any]:
    """List notable HTTP endpoints for quick discoverability."""
    return {
        "hooks": [
            "POST /hooks/{hook_name}",
        ],
        "health": "GET /health",
    }


# ---------------- Debug logs API and UI -----------------


class DebugEntry(BaseModel):
    """Row from debug_logs representing a single debug record."""

    id: str
    time_created: datetime
    debug_type_identifier: str
    jsonblob: dict[str, Any]


@app.get("/api/debug/{debug_type}", response_model=list[DebugEntry])
async def get_debug_entries(debug_type: str, limit: int = Query(default=50, le=500)) -> list[DebugEntry]:
    """Return latest debug entries for a given type (paged by limit)."""
    db_url = os.getenv("DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien")
    entries: list[DebugEntry] = []
    try:
        conn = await asyncpg.connect(db_url)
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
            await conn.close()
    except Exception as e:
        print(f"Error fetching debug logs: {e}")
    return entries


# --------- Dedicated debug browser with type selection + pagination ---------


class DebugTypeInfo(BaseModel):
    """Aggregated counts and latest timestamp per debug type."""

    debug_type_identifier: str
    count: int
    latest: datetime


@app.get("/api/debug/types", response_model=list[DebugTypeInfo])
async def get_debug_types() -> list[DebugTypeInfo]:
    """Return summary of available debug types with counts."""
    db_url = os.getenv("DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien")
    types: list[DebugTypeInfo] = []
    try:
        conn = await asyncpg.connect(db_url)
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
            await conn.close()
    except Exception as e:
        print(f"Error fetching debug types: {e}")
    return types


class DebugPage(BaseModel):
    """A simple paginated list of debug entries."""

    items: list[DebugEntry]
    page: int
    page_size: int
    total: int


@app.get("/api/debug/{debug_type}/page", response_model=DebugPage)
async def get_debug_page(
    debug_type: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> DebugPage:
    """Return a paginated slice of debug entries for a type."""
    db_url = os.getenv("DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien")
    items: list[DebugEntry] = []
    total = 0
    try:
        conn = await asyncpg.connect(db_url)
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
            await conn.close()
    except Exception as e:
        print(f"Error fetching debug page: {e}")
    return DebugPage(items=items, page=page, page_size=page_size, total=total)


@app.get("/api/hooks/counters")
async def get_hook_counters() -> dict[str, int]:
    """Expose in-memory hook counters for sanity/testing scripts."""
    return dict(_hook_counters)


@app.post("/hooks/{hook_name}")
async def hook_generic(hook_name: str, payload: dict[str, Any]) -> Any:
    """Generic hook endpoint for any CustomLogger hook.

    - Records in-memory log with name + payload
    - Inserts into debug_logs as debug_type=f"hook:{hook_name}"
    - Updates counters based on hook name
    - Returns a simple ack
    """
    try:
        record = {
            "hook": hook_name,
            "payload": payload,
        }
        # Log payloads so they're visible in docker logs
        _logger.debug("hook=%s payload=%s", hook_name, json.dumps(payload, ensure_ascii=False))
        # extract litellm_call_id deterministically from payload
        try:
            call_id = extract_call_id_for_hook(hook_name, payload)
            if isinstance(call_id, str) and call_id:
                record["litellm_call_id"] = call_id
        except Exception:
            pass
        _hook_logs.append(record)
        # Insert into DB without blocking the response path
        # Best-effort debug logging; failures are handled inside _insert_debug
        asyncio.create_task(_insert_debug(f"hook:{hook_name}", record))
        name = hook_name.lower()
        # Counter heuristics
        _hook_counters[name] += 1
        # now we call the appropriate function on the currently loaded policy
        handler = cast(
            Optional[Callable[..., Awaitable[Any]]],
            getattr(active_policy, name, None) if active_policy else None,
        )
        payload.pop("post_time_ns", None)  # remove this internal field if present
        if handler:
            return await handler(**payload)
        return payload
    except Exception as e:
        _logger.error(f"hook_generic_error: {e}")
        raise HTTPException(status_code=500, detail=f"hook_generic_error: {e}")


# ---------------- Hook testing orchestrator -----------------


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


def _parse_jsonblob(raw: Any) -> dict[str, Any]:
    """Return a dict for a row's jsonblob without raising.

    Why: DB rows may store JSON as `text`. Keep parsing logic isolated and
    predictable.
    """
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


@app.get("/api/hooks/trace_by_call_id", response_model=TraceResponse)
async def trace_by_call_id(call_id: str = Query(..., min_length=4)) -> TraceResponse:
    """Return ordered hook entries from debug_logs for a litellm_call_id.

    This endpoint intentionally excludes request_logs to keep the UI focused on
    debugging hook invocations without mixing in policy persistence.
    """
    db_url = os.getenv("DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien")
    entries: list[TraceEntry] = []
    try:
        conn = await asyncpg.connect(db_url)
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
            for r in rows:
                jb = _parse_jsonblob(r["jsonblob"])
                entries.append(
                    TraceEntry(
                        time=r["time_created"],
                        post_time_ns=_extract_post_ns(jb),
                        hook=jb.get("hook"),
                        debug_type=r["debug_type_identifier"],
                        payload=jb,
                    )
                )
        finally:
            await conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"trace_error: {e}")

    # Prefer high-resolution time; fallback to DB time converted to ns
    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return TraceResponse(call_id=call_id, entries=entries)


class CallIdInfo(BaseModel):
    """Summary row for a recent litellm_call_id with counts and latest time."""

    call_id: str
    count: int
    latest: datetime


@app.get("/api/hooks/recent_call_ids", response_model=list[CallIdInfo])
async def recent_call_ids(limit: int = Query(default=50, ge=1, le=500)) -> list[CallIdInfo]:
    """Return recent call IDs observed in debug logs with usage counts."""
    db_url = os.getenv("DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien")
    out: list[CallIdInfo] = []
    try:
        conn = await asyncpg.connect(db_url)
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
            for r in rows:
                cid = r["cid"]
                if not cid:
                    continue
                out.append(CallIdInfo(call_id=cid, count=int(r["cnt"]), latest=r["latest"]))
        finally:
            await conn.close()
    except Exception as e:
        print(f"Error fetching recent call ids: {e}")
    return out
