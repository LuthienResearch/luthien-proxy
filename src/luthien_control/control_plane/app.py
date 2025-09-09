# ABOUTME: FastAPI application for the Luthien Control plane service that orchestrates AI control policies
# ABOUTME: Provides endpoints for policy evaluation, chunk monitoring, resampling, and trusted model interactions

import os
from typing import Any, Dict, Optional, List
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import asyncpg
import json

# Import our policy and monitoring modules (will implement these)
from luthien_control.policies.engine import PolicyEngine
from luthien_control.policies.base import LuthienPolicy
from luthien_control.policies.noop import NoOpPolicy
from luthien_control.control_plane.ui import router as ui_router
import yaml


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


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    global policy_engine, active_policy

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
            "POST /hooks/stream_chunk",
            "POST /hooks/stream_replacement",
        ],
        "health": "GET /health",
    }


# ---------------- Removed hook-style endpoints (not used) -----------------


# ---------------- Logging UI endpoints -----------------


class LogEntry(BaseModel):
    """Formatted log entry for UI display."""

    id: str
    episode_id: Optional[str]
    step_id: Optional[str]
    call_type: Optional[str]
    stage: str
    request_summary: str
    response_summary: Optional[str]
    policy_action: Optional[str]
    created_at: datetime


@app.get("/api/logs", response_model=List[LogEntry])
async def get_logs(limit: int = Query(default=50, le=500)):
    """Fetch recent request logs from the database."""
    db_url = os.getenv(
        "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
    )

    logs = []
    try:
        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                """
                SELECT id, episode_id, step_id, call_type, stage,
                       request, response, policy_action, created_at
                FROM request_logs
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )

            for row in rows:
                # Extract key info from request JSON
                request_data = json.loads(row["request"]) if row["request"] else {}
                messages = request_data.get("messages", [])
                request_summary = "No messages"
                if messages:
                    last_message = (
                        messages[-1] if isinstance(messages, list) else messages
                    )
                    if isinstance(last_message, dict):
                        content = last_message.get("content", "")
                        request_summary = (
                            content[:100] + "..." if len(content) > 100 else content
                        )

                # Extract key info from response JSON
                response_summary = None
                if row["response"]:
                    response_data = json.loads(row["response"])
                    if "choices" in response_data:
                        choices = response_data["choices"]
                        if choices and len(choices) > 0:
                            content = choices[0].get("message", {}).get("content", "")
                            response_summary = (
                                content[:100] + "..." if len(content) > 100 else content
                            )
                    elif "accumulated_length" in response_data:
                        response_summary = f"Streaming chunk (accumulated: {response_data['accumulated_length']} chars)"

                logs.append(
                    LogEntry(
                        id=str(row["id"]),
                        episode_id=str(row["episode_id"])
                        if row["episode_id"]
                        else None,
                        step_id=str(row["step_id"]) if row["step_id"] else None,
                        call_type=row["call_type"],
                        stage=row["stage"],
                        request_summary=request_summary,
                        response_summary=response_summary,
                        policy_action=row["policy_action"],
                        created_at=row["created_at"],
                    )
                )
        finally:
            await conn.close()
    except Exception as e:
        print(f"Error fetching logs: {e}")

    return logs


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
