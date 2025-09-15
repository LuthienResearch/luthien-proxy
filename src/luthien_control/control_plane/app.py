# ABOUTME: FastAPI application for the Luthien Control plane service that orchestrates AI control policies
# ABOUTME: Provides endpoints for policy evaluation, chunk monitoring, resampling, and trusted model interactions

import os
from typing import Any, Dict, Optional, List
from datetime import datetime

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
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
    extract_call_id_from_request_data,
)
from luthien_control.control_plane.utils.streaming import extract_delta_text


# Pydantic models for API requests/responses
class PolicyEvaluationRequest(BaseModel):
    """Request for policy evaluation at different stages."""

    stage: str = Field(..., description="Stage: pre, post, or streaming_chunk")
    episode_id: Optional[str] = None
    step_id: Optional[str] = None
    call_type: Optional[str] = None
    request: Dict[str, Any] = Field(..., description="Original LLM request")
    response: Optional[Dict[str, Any]] = None
    user_metadata: Dict[str, Any] = Field(default_factory=dict)


class PolicyEvaluationResponse(BaseModel):
    """Response from policy evaluation."""

    action: str = Field(
        ..., description="Action to take: allow, reject, rewrite, replace_response"
    )
    reject: bool = Field(default=False)
    reject_message: Optional[str] = None
    rewrite: Optional[Dict[str, Any]] = None
    replace_response: Optional[Dict[str, Any]] = None
    trigger_resample: bool = Field(default=False)
    resample_config: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChunkEvaluationRequest(BaseModel):
    """Request for evaluating streaming chunks."""

    episode_id: Optional[str] = None
    step_id: Optional[str] = None
    accumulated_text: str = Field(..., description="Text accumulated so far")
    latest_chunk: str = Field(..., description="Most recent chunk")
    request: Dict[str, Any] = Field(..., description="Original request context")


class ChunkEvaluationResponse(BaseModel):
    """Response for chunk evaluation."""

    halt_stream: bool = Field(default=False, description="Whether to halt the stream")
    switch_to_trusted: bool = Field(
        default=False, description="Switch to trusted model"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResampleRequest(BaseModel):
    """Request for defer-to-resample protocol."""

    episode_id: str
    step_id: str
    original_request: Dict[str, Any]
    original_response: Dict[str, Any]
    resample_config: Dict[str, Any] = Field(default_factory=dict)


class ResampleResponse(BaseModel):
    """Response from resampling protocol."""

    replacement_response: Optional[Dict[str, Any]] = None
    audit_required: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TrustedStreamRequest(BaseModel):
    """Request for trusted stream replacement."""

    episode_id: str
    step_id: str
    original_request: Dict[str, Any]
    partial_content: str


# FastAPI app setup
app = FastAPI(
    title="Luthien Control Plane",
    description="AI Control policy orchestration service",
    version="0.1.0",
)

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
_hook_counters: Dict[str, int] = {
    "hook_pre": 0,
    "hook_post_success": 0,
    "hook_stream_chunk": 0,
}
_hook_logs: deque = deque(maxlen=500)


def _get_in(d: Dict[str, Any], path: list[str]) -> Optional[Any]:
    # Kept for backward-compat; new code uses utils.hooks._get_in indirectly
    cur: Any = d
    try:
        for k in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur
    except Exception:
        return None


def _extract_call_id_from_stream_payload(payload: Dict[str, Any]) -> Optional[str]:
    return extract_call_id_from_request_data(payload.get("request_data") or {})


# No explicit clear-on-finish; rely on TTL for simplicity.


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
            "POST /hooks/pre",
            "POST /hooks/post_success",
            "POST /hooks/{hook_name}",
            "POST /hooks/stream_replacement",
        ],
        "health": "GET /health",
    }


# ---------------- Hook-style endpoints (LiteLLM callback integration) -----------------


@app.post("/hooks/post_success")
async def hook_post_success(payload: Dict[str, Any]):
    """Post-success hook. Can request response replacement.

    Expects payload with keys:
      - data: dict (original request)
      - user_api_key_dict: Optional[dict]
      - response: dict (original response)

    Returns:
      - {replace: false} to keep original
      - {replace: true, replacement: dict} to replace
    """
    global active_policy
    if active_policy is None:
        active_policy = NoOpPolicy()

    data = payload.get("data") or {}
    user_api_key_dict = payload.get("user_api_key_dict")
    response = payload.get("response") or {}

    try:
        replacement = await active_policy.async_post_call_success_hook(
            data=data, user_api_key_dict=user_api_key_dict, response=response
        )
        _hook_counters["hook_post_success"] = (
            _hook_counters.get("hook_post_success", 0) + 1
        )
        if replacement is None:
            return {"replace": False}
        return {"replace": True, "replacement": replacement}
    except Exception as e:
        return {"replace": True, "replacement": {"error": str(e)}}


# Removed /hooks/stream_chunk. Streaming chunks are ingested via generic hooks.


# ---------------- Debug logs API and UI -----------------


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


class DebugLogIn(BaseModel):
    debug_type: str
    payload: Dict[str, Any]


@app.post("/api/debug/log")
async def post_debug_log(entry: DebugLogIn):
    await _insert_debug(entry.debug_type, entry.payload)
    return {"ok": True}


class HookLogIn(BaseModel):
    hook: str
    kwargs: Dict[str, Any] | None = None
    response_obj: Dict[str, Any] | str | None = None


@app.post("/api/hooks/log")
async def post_hook_log(entry: HookLogIn):
    item = entry.model_dump()
    # enrich with call_id for easier tracing (best-effort)
    try:
        call_id = (
            _get_in(item, ["kwargs", "request_data", "litellm_call_id"])
            or _get_in(item, ["kwargs", "kwargs", "litellm_call_id"])
            or _get_in(item, ["kwargs", "litellm_call_id"])
            or None
        )
        if isinstance(call_id, str) and call_id:
            item.setdefault("litellm_call_id", call_id)
    except Exception:
        pass
    _hook_logs.append(item)
    return {"ok": True, "count": len(_hook_logs)}


@app.get("/api/hooks/logs")
async def get_hook_logs(limit: int = Query(default=50, ge=1, le=500)):
    items = list(_hook_logs)[-limit:]
    return items


@app.delete("/api/hooks/logs")
async def clear_hook_logs():
    _hook_logs.clear()
    return {"ok": True, "count": 0}


class HookIngest(BaseModel):
    hook: str
    kwargs: Dict[str, Any] | None = None
    response_obj: Dict[str, Any] | str | None = None


@app.post("/hooks/ingest")
async def ingest_generic_hook(entry: HookIngest):
    """Generic ingestion for any LiteLLM logger hook.

    - Stores in-memory log
    - Inserts into debug_logs with debug_type 'litellm_hook'
    - Updates counters heuristically
    """
    item = entry.model_dump()
    _hook_logs.append(item)

    # Insert into DB debug logs (best-effort) using a consistent hook:* type
    try:
        await _insert_debug(f"hook:{entry.hook}", item)
    except Exception:
        pass

    name = (entry.hook or "").lower()
    # Heuristic mapping to our three counters
    try:
        if any(
            k in name for k in ["pre_call_hook", "log_pre_api_call", "async_log_pre_"]
        ):
            _hook_counters["hook_pre"] = _hook_counters.get("hook_pre", 0) + 1
        if any(
            k in name
            for k in [
                "post_call_success_hook",
                "log_post_api_call",
                "log_success_event",
                "async_log_success_event",
            ]
        ):
            _hook_counters["hook_post_success"] = (
                _hook_counters.get("hook_post_success", 0) + 1
            )
        if any(
            k in name
            for k in [
                "stream_event",
                "streaming_iterator_hook",
                "post_call_streaming_hook",
            ]
        ):
            _hook_counters["hook_stream_chunk"] = (
                _hook_counters.get("hook_stream_chunk", 0) + 1
            )
    except Exception:
        pass

    return {"ok": True}


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
        # extract litellm_call_id deterministically from payload
        try:
            call_id = extract_call_id_for_hook(hook_name, payload)
            if isinstance(call_id, str) and call_id:
                record["litellm_call_id"] = call_id
        except Exception:
            pass
        _hook_logs.append(record)
        # Insert into DB
        await _insert_debug(f"hook:{hook_name}", record)
        name = hook_name.lower()
        # Counter heuristics
        if any(
            k in name
            for k in [
                "pre_call_hook",
                "pre_api_call",
                "pre_routing",
                "pre_call_deployment",
            ]
        ):
            _hook_counters["hook_pre"] = _hook_counters.get("hook_pre", 0) + 1
        if any(
            k in name
            for k in [
                "post_call_success_hook",
                "post_api_call",
                "success_event",
                "logging_hook",
                "post_call_success_deployment_hook",
            ]
        ):
            _hook_counters["hook_post_success"] = (
                _hook_counters.get("hook_post_success", 0) + 1
            )
        if any(
            k in name
            for k in [
                "stream_event",
                "post_call_streaming",
                "streaming_iterator_hook",
                "output_params_streaming",
            ]
        ):
            _hook_counters["hook_stream_chunk"] = (
                _hook_counters.get("hook_stream_chunk", 0) + 1
            )
        # Streaming context ingest for iterator/stream events
        if any(
            k in name
            for k in [
                "streaming_iterator_hook",
                "stream_event",
                "post_call_streaming_hook",
            ]
        ):
            try:
                if not stream_store:
                    raise RuntimeError("stream_store not initialized")
                call_id = extract_call_id_for_hook(hook_name, payload)
                # response_obj carries the raw chunk
                chunk_obj = payload.get("response_obj")
                if isinstance(chunk_obj, str):
                    # best-effort: ignore non-dict
                    chunk_obj = None
                if isinstance(chunk_obj, dict):
                    delta = extract_delta_text(chunk_obj)
                    if delta:
                        await stream_store.append_delta(call_id, delta)
            except Exception as ie:
                print(f"stream_context_ingest_error: {ie}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"hook_generic_error: {e}")


# ---------------- Hook testing orchestrator -----------------


class HookTestRequest(BaseModel):
    model: Optional[str] = None
    prompt: str = "Say TEST_OK"
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int = 50


class HookTestResult(BaseModel):
    ok: bool
    counters: Dict[str, int]
    response_preview: Optional[str] = None
    error: Optional[str] = None


@app.post("/tests/run", response_model=HookTestResult)
async def run_hook_test(req: HookTestRequest):
    """Execute a simple request via the proxy and report which hooks fired.

    Uses LITELLM_URL (default http://litellm-proxy:4000) and LITELLM_MASTER_KEY
    for authorization. Resets in-memory hook counters before issuing the test request.
    """
    # Reset counters
    for k in list(_hook_counters.keys()):
        _hook_counters[k] = 0
    _hook_logs.clear()

    proxy_url = os.getenv("LITELLM_URL", "http://litellm-proxy:4000")
    master_key = os.getenv("LITELLM_MASTER_KEY", "sk-luthien-dev-key")
    model = req.model or os.getenv("TEST_MODEL", "gpt-4o")

    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": req.prompt}],
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
    }
    # no correlation; rely on litellm_call_id emitted by proxy
    try:
        import httpx

        if req.stream:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST",
                    f"{proxy_url}/chat/completions",
                    headers=headers,
                    json={**payload, "stream": True},
                ) as resp:
                    if resp.status_code != 200:
                        text = await resp.aread()
                        raise HTTPException(
                            status_code=502,
                            detail=f"proxy_stream_error: {resp.status_code}: {text.decode(errors='ignore')}",
                        )
                    parts: list[str] = []
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                                delta = (
                                    obj.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content")
                                )
                                if delta:
                                    parts.append(delta)
                            except Exception:
                                pass
                    preview = "".join(parts)[:200] if parts else None
                    return HookTestResult(
                        ok=True,
                        counters=dict(_hook_counters),
                        response_preview=preview,
                        error=None,
                    )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{proxy_url}/chat/completions", headers=headers, json=payload
                )
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail=f"proxy_error: {resp.status_code}: {resp.text}",
                    )
                data = resp.json()
                preview = data.get("choices", [{}])[0].get("message", {}).get("content")
                if isinstance(preview, str):
                    preview = preview[:200]
                return HookTestResult(
                    ok=True,
                    counters=dict(_hook_counters),
                    response_preview=preview,
                    error=None,
                )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"cp_test_error: {e}")


@app.get("/api/hooks/counters")
async def get_hook_counters():
    """In-memory counters for verifying that hooks are being invoked."""
    return dict(_hook_counters)


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
