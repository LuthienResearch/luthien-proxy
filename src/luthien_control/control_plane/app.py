# ABOUTME: FastAPI application for the Luthien Control plane service that orchestrates AI control policies
# ABOUTME: Provides endpoints for litellm hooks, as well as luthien-specific utilities and UI

from collections import Counter
import asyncio
import os
from typing import Any, Dict, Optional, List
from datetime import datetime
import logging
import sys

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncpg
import json
from collections import deque

# Import our policy and monitoring modules (will implement these)
from luthien_control.policies.engine import PolicyEngine
from luthien_control.policies.base import LuthienPolicy
from luthien_control.policies.noop import NoOpPolicy
from luthien_control.control_plane.ui import router as ui_router
import yaml
from luthien_control.control_plane.stream_context import StreamContextStore
from luthien_control.control_plane.utils.hooks import (
    extract_call_id_for_hook,
)


# FastAPI app setup
app = FastAPI(
    title="Luthien Control Plane",
    description="AI Control policy orchestration service",
    version="0.1.0",
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


async def _insert_debug(debug_type: str, payload: Dict[str, Any]) -> None:
    """Insert a debug log row into the database (best-effort)."""
    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )
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


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    global policy_engine, active_policy, stream_store

    try:
        # Initialize policy engine only if persistence is configured (optional)
        if os.getenv("DATABASE_URL") or os.getenv("REDIS_URL"):
            policy_engine = PolicyEngine(
                database_url=os.getenv("DATABASE_URL"),
                redis_url=os.getenv("REDIS_URL"),
            )
            await policy_engine.initialize()

        # Load active policy via config file (LUTHIEN_POLICY_CONFIG)
        active_policy = _load_policy_from_config()

        # Initialize stream context store (Redis required; fail fast otherwise)
        if not policy_engine or not policy_engine.redis_client:
            raise RuntimeError(
                "Redis is required for streaming context; set REDIS_URL and ensure connectivity"
            )
        stream_store = StreamContextStore(
            redis_client=policy_engine.redis_client,
            ttl_seconds=int(os.getenv("STREAM_CONTEXT_TTL", "3600")),
        )

        print("Control plane services initialized successfully")

    except Exception as e:
        print(f"Error initializing control plane: {e}")
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "luthien-control-plane", "version": "0.1.0"}


def _load_policy_from_config() -> LuthienPolicy:
    """Load policy from YAML config specified by LUTHIEN_POLICY_CONFIG.

    Expected YAML structure (consolidated):
      policy: "module.path:ClassName"            # required
      policy_options: { ... }                     # optional, inline

    Falls back to /app/config/luthien_config.yaml if env var is not set.
    If anything fails, returns NoOpPolicy.
    """
    config_path = os.getenv("LUTHIEN_POLICY_CONFIG", "/app/config/luthien_config.yaml")
    policy_ref: Optional[str] = None
    policy_options: Optional[Dict[str, Any]] = None

    try:
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
                policy_ref = cfg.get("policy")
                policy_options = cfg.get("policy_options") or None
        else:
            print(f"Policy config not found at {config_path}; using NoOpPolicy")
    except Exception as e:
        print(f"Failed to read policy config {config_path}: {e}")

    if not policy_ref:
        print("No policy specified in config; using NoOpPolicy")
        return NoOpPolicy()

    try:
        module_path, class_name = policy_ref.split(":", 1)
        module = __import__(module_path, fromlist=[class_name])
        cls = getattr(module, class_name)
        if not issubclass(cls, LuthienPolicy):
            print(
                f"Configured policy {class_name} does not subclass LuthienPolicy; using NoOpPolicy"
            )
            return NoOpPolicy()
        print(f"Loaded policy from config: {class_name} ({module_path})")
        # Prefer passing inline policy_options if the class accepts it; otherwise fallback
        try:
            if policy_options is not None:
                return cls(options=policy_options)
        except TypeError:
            pass
        # Back-compat for policies expecting env-provided options
        if policy_options is not None:
            try:
                os.environ["LUTHIEN_POLICY_OPTIONS_JSON"] = json.dumps(policy_options)
            except Exception:
                pass
        return cls()
    except Exception as e:
        print(f"Failed to load policy '{policy_ref}': {e}. Using NoOpPolicy.")
        return NoOpPolicy()


@app.get("/endpoints")
async def list_endpoints():
    return {
        "hooks": [
            "POST /hooks/{hook_name}",
        ],
        "health": "GET /health",
    }


# ---------------- Debug logs API and UI -----------------


class DebugEntry(BaseModel):
    id: str
    time_created: datetime
    debug_type_identifier: str
    jsonblob: Dict[str, Any]


@app.get("/api/debug/{debug_type}", response_model=List[DebugEntry])
async def get_debug_entries(debug_type: str, limit: int = Query(default=50, le=500)):
    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )
    entries: List[DebugEntry] = []
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
    debug_type_identifier: str
    count: int
    latest: datetime


@app.get("/api/debug/types", response_model=List[DebugTypeInfo])
async def get_debug_types():
    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )
    types: List[DebugTypeInfo] = []
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
    items: List[DebugEntry]
    page: int
    page_size: int
    total: int


@app.get("/api/debug/{debug_type}/page", response_model=DebugPage)
async def get_debug_page(
    debug_type: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
):
    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )
    items: List[DebugEntry] = []
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
async def get_hook_counters():
    """Expose in-memory hook counters for sanity/testing scripts."""
    return dict(_hook_counters)


@app.post("/hooks/{hook_name}")
async def hook_generic(hook_name: str, payload: Dict[str, Any]):
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
        _logger.debug(
            "hook=%s payload=%s", hook_name, json.dumps(payload, ensure_ascii=False)
        )
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
        fn = getattr(active_policy, name, None)
        payload.pop("post_time_ns", None)  # remove this internal field if present
        if fn and callable(fn):
            return await fn(**payload)
        return payload
    except Exception as e:
        _logger.error(f"hook_generic_error: {e}")
        raise HTTPException(status_code=500, detail=f"hook_generic_error: {e}")


# ---------------- Hook testing orchestrator -----------------


class TraceEntry(BaseModel):
    time: datetime
    post_time_ns: Optional[int] = None
    hook: Optional[str] = None
    debug_type: Optional[str] = None
    payload: Dict[str, Any]


class TraceResponse(BaseModel):
    call_id: str
    entries: List[TraceEntry]


@app.get("/api/hooks/trace_by_call_id", response_model=TraceResponse)
async def trace_by_call_id(call_id: str = Query(..., min_length=4)):
    """Return ordered hook entries from debug_logs for a litellm_call_id.

    This endpoint intentionally excludes request_logs to keep the UI focused on
    debugging hook invocations without mixing in policy persistence.
    """
    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )
    entries: List[TraceEntry] = []
    try:
        conn = await asyncpg.connect(db_url)
        try:
            # Fetch hook entries from debug_logs only
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
                jb = r["jsonblob"]
                if isinstance(jb, str):
                    try:
                        jb = json.loads(jb)
                    except Exception:
                        jb = {"raw": jb}
                # Prefer high-resolution time from payload if present
                post_ns = None
                try:
                    if isinstance(jb, dict):
                        pl = jb.get("payload")
                        if isinstance(pl, dict):
                            ns = pl.get("post_time_ns")
                            if isinstance(ns, int):
                                post_ns = ns
                            elif isinstance(ns, float):
                                post_ns = int(ns)
                except Exception:
                    post_ns = None
                entries.append(
                    TraceEntry(
                        time=r["time_created"],
                        post_time_ns=post_ns,
                        hook=(jb.get("hook") if isinstance(jb, dict) else None),
                        debug_type=r["debug_type_identifier"],
                        payload=jb,
                    )
                )
        finally:
            await conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"trace_error: {e}")

    # Sort by high-resolution time when available, else fallback to DB time
    def sort_key(e: TraceEntry) -> int:
        if e.post_time_ns is not None:
            return e.post_time_ns
        try:
            # Convert datetime to ns epoch
            return int(e.time.timestamp() * 1_000_000_000)
        except Exception:
            return 0

    entries.sort(key=sort_key)
    return TraceResponse(call_id=call_id, entries=entries)


class CallIdInfo(BaseModel):
    call_id: str
    count: int
    latest: datetime


@app.get("/api/hooks/recent_call_ids", response_model=List[CallIdInfo])
async def recent_call_ids(limit: int = Query(default=50, ge=1, le=500)):
    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )
    out: List[CallIdInfo] = []
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
                out.append(
                    CallIdInfo(call_id=cid, count=int(r["cnt"]), latest=r["latest"])
                )
        finally:
            await conn.close()
    except Exception as e:
        print(f"Error fetching recent call ids: {e}")
    return out
