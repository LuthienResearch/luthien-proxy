# ABOUTME: FastAPI application for the Luthien Control plane service that orchestrates AI control policies
# ABOUTME: Provides endpoints for policy evaluation, chunk monitoring, resampling, and trusted model interactions

import os
from typing import Any, Dict, Optional

from beartype import beartype
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# Import our policy and monitoring modules (will implement these)
from luthien_control.policies.engine import PolicyEngine
from luthien_control.policies.base import LuthienPolicy
from luthien_control.policies.noop import NoOpPolicy


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

        # Load active policy
        active_policy = _load_policy_from_env()

        print("Control plane services initialized successfully")

    except Exception as e:
        print(f"Error initializing control plane: {e}")
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "luthien-control-plane", "version": "0.1.0"}


def _load_policy_from_env() -> LuthienPolicy:
    """Load a policy class from LUTHIEN_POLICY env var or use NoOpPolicy."""
    policy_path = os.getenv("LUTHIEN_POLICY")
    if not policy_path:
        return NoOpPolicy()

    try:
        module_path, class_name = policy_path.split(":", 1)
        module = __import__(module_path, fromlist=[class_name])
        cls = getattr(module, class_name)
        if not issubclass(cls, LuthienPolicy):
            print("LUTHIEN_POLICY does not subclass LuthienPolicy; using NoOpPolicy")
            return NoOpPolicy()
        return cls()
    except Exception as e:
        print(f"Failed to load LUTHIEN_POLICY '{policy_path}': {e}. Using NoOpPolicy.")
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


# ---------------- Hook-style endpoints (mirror LiteLLM) -----------------


class PreHookRequest(BaseModel):
    user_api_key_dict: Optional[dict] = None
    cache: Optional[dict] = None
    data: dict
    call_type: Optional[str] = None


class PreHookResponse(BaseModel):
    # one of: none | string | dict
    result_type: str
    string: Optional[str] = None
    dict: Optional[dict] = None


@app.post("/hooks/pre", response_model=PreHookResponse)
@beartype
async def hook_pre(request: PreHookRequest) -> PreHookResponse:
    if not active_policy:
        return PreHookResponse(result_type="none")

    result = await active_policy.async_pre_call_hook(
        request.user_api_key_dict, request.cache, request.data, request.call_type
    )
    if result is None:
        return PreHookResponse(result_type="none")
    if isinstance(result, str):
        return PreHookResponse(result_type="string", string=result)
    if isinstance(result, dict):
        return PreHookResponse(result_type="dict", dict=result)
    return PreHookResponse(result_type="none")


class PostHookRequest(BaseModel):
    data: dict
    user_api_key_dict: Optional[dict] = None
    response: dict


class PostHookResponse(BaseModel):
    replace: bool = False
    replacement: Optional[dict] = None


@app.post("/hooks/post_success", response_model=PostHookResponse)
@beartype
async def hook_post_success(request: PostHookRequest) -> PostHookResponse:
    if not active_policy:
        return PostHookResponse(replace=False)

    replacement = await active_policy.async_post_call_success_hook(
        request.data, request.user_api_key_dict, request.response
    )
    if isinstance(replacement, dict):
        return PostHookResponse(replace=True, replacement=replacement)
    return PostHookResponse(replace=False)


class StreamChunkRequest(BaseModel):
    user_api_key_dict: Optional[dict] = None
    request_data: dict
    chunk: dict
    chunk_index: int
    accumulated_text: str


class StreamChunkResponse(BaseModel):
    action: str  # pass | suppress | edit | replace_stream
    chunk: Optional[dict] = None


@app.post("/hooks/stream_chunk", response_model=StreamChunkResponse)
@beartype
async def hook_stream_chunk(request: StreamChunkRequest) -> StreamChunkResponse:
    if not active_policy:
        return StreamChunkResponse(action="pass")

    decision = await active_policy.streaming_on_chunk(
        request.user_api_key_dict,
        request.request_data,
        request.chunk,
        request.chunk_index,
        request.accumulated_text,
    )

    action = decision.get("action", "pass")
    chunk = decision.get("chunk") if action == "edit" else None
    return StreamChunkResponse(action=action, chunk=chunk)


class StreamReplacementRequest(BaseModel):
    request_data: dict


@app.post("/hooks/stream_replacement")
@beartype
async def hook_stream_replacement(request: StreamReplacementRequest):
    if not active_policy:

        async def empty():
            if False:
                yield {}

        return StreamingResponse(empty(), media_type="text/event-stream")

    import json

    async def gen():
        async for chunk in active_policy.streaming_replacement(request.request_data):
            yield json.dumps(chunk) + "\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
