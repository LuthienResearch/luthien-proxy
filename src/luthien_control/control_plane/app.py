# ABOUTME: FastAPI application for the Luthien Control plane service that orchestrates AI control policies
# ABOUTME: Provides endpoints for policy evaluation, chunk monitoring, resampling, and trusted model interactions

import os
from typing import Any, Dict, List, Optional

from beartype import beartype
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Import our policy and monitoring modules (will implement these)
from luthien_control.policies.engine import PolicyEngine
from luthien_control.monitors.trusted import TrustedMonitor
from luthien_control.monitors.untrusted import UntrustedMonitor


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
trusted_monitor: Optional[TrustedMonitor] = None
untrusted_monitor: Optional[UntrustedMonitor] = None


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    global policy_engine, trusted_monitor, untrusted_monitor

    try:
        # Initialize policy engine
        policy_engine = PolicyEngine(
            database_url=os.getenv("DATABASE_URL"), redis_url=os.getenv("REDIS_URL")
        )
        await policy_engine.initialize()

        # Initialize monitors
        trusted_monitor = TrustedMonitor(
            model_config={
                "model": "ollama/llama3.1:8b",
                "api_base": "http://ollama:11434",
            }
        )

        untrusted_monitor = UntrustedMonitor()

        print("Control plane services initialized successfully")

    except Exception as e:
        print(f"Error initializing control plane: {e}")
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "luthien-control-plane", "version": "0.1.0"}


@app.post("/policy/evaluate", response_model=PolicyEvaluationResponse)
@beartype
async def evaluate_policy(request: PolicyEvaluationRequest) -> PolicyEvaluationResponse:
    """
    Main policy evaluation endpoint.
    Called by LiteLLM hooks for pre/post call decisions.
    """
    try:
        if not policy_engine:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Policy engine not initialized",
            )

        # Get policy for this request
        policy = await policy_engine.get_policy(
            episode_id=request.episode_id, user_metadata=request.user_metadata
        )

        if request.stage == "pre":
            return await _handle_pre_call(request, policy)
        elif request.stage == "post":
            return await _handle_post_call(request, policy)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown stage: {request.stage}",
            )

    except Exception as e:
        print(f"Error in policy evaluation: {e}")
        # Fail-safe: allow request to proceed
        return PolicyEvaluationResponse(action="allow")


@app.post("/policy/on_chunk", response_model=ChunkEvaluationResponse)
@beartype
async def evaluate_chunk(request: ChunkEvaluationRequest) -> ChunkEvaluationResponse:
    """
    Evaluate streaming chunks for potential intervention.
    Called periodically during streaming responses.
    """
    try:
        if not policy_engine or not trusted_monitor:
            return ChunkEvaluationResponse(halt_stream=False)

        # Get policy for this episode
        policy = await policy_engine.get_policy(episode_id=request.episode_id)

        # Score the accumulated content with trusted model
        suspiciousness_score = await trusted_monitor.score_text(
            text=request.accumulated_text, context=request.request
        )

        # Check if we should halt the stream
        halt_threshold = policy.get("streaming", {}).get("halt_threshold", 0.9)
        should_halt = suspiciousness_score >= halt_threshold

        if should_halt:
            # Log the intervention
            await policy_engine.log_decision(
                episode_id=request.episode_id,
                step_id=request.step_id,
                decision_type="streaming_halt",
                score=suspiciousness_score,
                threshold=halt_threshold,
            )

        return ChunkEvaluationResponse(
            halt_stream=should_halt,
            switch_to_trusted=should_halt,
            metadata={
                "suspiciousness_score": suspiciousness_score,
                "threshold": halt_threshold,
            },
        )

    except Exception as e:
        print(f"Error evaluating chunk: {e}")
        return ChunkEvaluationResponse(halt_stream=False)


@app.post("/resample", response_model=ResampleResponse)
@beartype
async def handle_resample(request: ResampleRequest) -> ResampleResponse:
    """
    Handle defer-to-resample protocol.
    Generates k alternative responses and selects the least suspicious.
    """
    try:
        if not policy_engine:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Policy engine not initialized",
            )

        # Get policy configuration
        policy = await policy_engine.get_policy(episode_id=request.episode_id)
        resample_config = policy.get("resample", {})

        if not resample_config.get("enabled", False):
            return ResampleResponse()

        # Generate k alternative responses
        k = resample_config.get("k", 3)
        alternatives = await _generate_alternatives(request.original_request, k=k)

        # Score all alternatives with trusted monitor
        scored_alternatives = []
        for alt in alternatives:
            score = await trusted_monitor.score_response(
                response=alt, context=request.original_request
            )
            scored_alternatives.append((score, alt))

        # Find minimum suspiciousness
        min_score, best_alternative = min(scored_alternatives, key=lambda x: x[0])

        # Decide whether to use alternative or trigger audit
        audit_threshold = resample_config.get("audit_if_min_score_gt", 0.85)

        if min_score >= audit_threshold:
            # Trigger human audit
            await policy_engine.trigger_audit(
                episode_id=request.episode_id,
                step_id=request.step_id,
                reason="high_suspiciousness_after_resample",
                score=min_score,
            )
            return ResampleResponse(audit_required=True)
        else:
            # Use the best alternative
            return ResampleResponse(
                replacement_response=best_alternative,
                metadata={
                    "original_score": scored_alternatives[0][0],  # Score of original
                    "min_score": min_score,
                    "alternatives_count": len(alternatives),
                },
            )

    except Exception as e:
        print(f"Error in resample protocol: {e}")
        return ResampleResponse()


@app.post("/trusted/stream_replacement")
@beartype
async def stream_trusted_replacement(request: TrustedStreamRequest):
    """
    Generate a streaming replacement using trusted model.
    Called when original stream is halted.
    """
    try:
        if not trusted_monitor:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Trusted monitor not available",
            )

        # Generate trusted response as a stream
        async for chunk in trusted_monitor.generate_stream(
            request=request.original_request,
            context=f"Previous partial output: {request.partial_content}",
        ):
            yield chunk

    except Exception as e:
        print(f"Error generating trusted stream: {e}")
        # Return error message as stream
        yield {
            "choices": [
                {
                    "delta": {
                        "content": "[Error: Unable to generate trusted replacement]"
                    },
                    "index": 0,
                    "finish_reason": "error",
                }
            ]
        }


# Helper functions
@beartype
async def _handle_pre_call(
    request: PolicyEvaluationRequest, policy: Dict[str, Any]
) -> PolicyEvaluationResponse:
    """Handle pre-call policy evaluation."""
    # For now, implement basic pass-through with metadata addition
    # TODO: Add tool gating, prompt hardening, etc.

    return PolicyEvaluationResponse(
        action="allow",
        metadata={
            "policy_applied": policy.get("name", "default"),
            "episode_id": request.episode_id,
        },
    )


@beartype
async def _handle_post_call(
    request: PolicyEvaluationRequest, policy: Dict[str, Any]
) -> PolicyEvaluationResponse:
    """Handle post-call policy evaluation."""
    if not request.response or not trusted_monitor:
        return PolicyEvaluationResponse(action="allow")

    # Score response with trusted monitor
    score = await trusted_monitor.score_response(
        response=request.response, context=request.request
    )

    # Check if we should defer to trusted model
    defer_threshold = policy.get("defer_threshold_T", 0.7)

    if score >= defer_threshold:
        # Generate trusted replacement
        trusted_response = await trusted_monitor.generate_response(
            request=request.request, context="Generate a safe response for this request"
        )

        # Log the intervention
        await policy_engine.log_decision(
            episode_id=request.episode_id,
            step_id=request.step_id,
            decision_type="trusted_replacement",
            score=score,
            threshold=defer_threshold,
        )

        return PolicyEvaluationResponse(
            action="replace_response",
            replace_response=trusted_response,
            metadata={
                "suspiciousness_score": score,
                "threshold": defer_threshold,
                "intervention": "trusted_replacement",
            },
        )

    # Check if we should trigger resampling
    resample_config = policy.get("resample", {})
    if resample_config.get("enabled", False) and score >= resample_config.get(
        "trigger_threshold", 0.6
    ):
        return PolicyEvaluationResponse(
            action="trigger_resample",
            trigger_resample=True,
            resample_config=resample_config,
            metadata={
                "suspiciousness_score": score,
                "trigger_threshold": resample_config.get("trigger_threshold", 0.6),
            },
        )

    return PolicyEvaluationResponse(
        action="allow",
        metadata={"suspiciousness_score": score, "threshold": defer_threshold},
    )


@beartype
async def _generate_alternatives(
    original_request: Dict[str, Any], k: int = 3
) -> List[Dict[str, Any]]:
    """Generate k alternative responses for resampling."""
    # This is a stub - in practice, would call the same model k times
    # with slight variations (temperature, top_p, etc.)

    alternatives = []
    for i in range(k):
        # For now, return mock alternatives
        # TODO: Implement actual LLM calls with variation
        alternatives.append(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"[Alternative response {i + 1} - not yet implemented]",
                        }
                    }
                ]
            }
        )

    return alternatives
