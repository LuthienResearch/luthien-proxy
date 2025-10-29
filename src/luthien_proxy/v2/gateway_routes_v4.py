# ABOUTME: V4 gateway routes using PolicyOrchestrator refactor
# ABOUTME: New /v4/* endpoints for testing the refactored pipeline

"""V4 LLM gateway API routes using PolicyOrchestrator.

This module provides new /v4/* endpoints that use the refactored pipeline:
- PolicyOrchestrator for orchestration
- SimplePolicy for easy policy authoring
- ObservabilityContext for unified observability
- TransactionRecorder for automatic recording

These routes run in parallel with existing /v1/* routes for gradual migration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry import trace

from luthien_proxy.utils import db
from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.messages import Request as RequestMessage
from luthien_proxy.v2.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.v2.orchestration.factory import create_default_orchestrator
from luthien_proxy.v2.policies.simple_policy import SimplePolicy

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(tags=["gateway-v4"], prefix="/v4")
security = HTTPBearer(auto_error=False)


# === AUTH (reused from gateway_routes.py) ===
def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Verify API key from either Authorization header or x-api-key header."""
    api_key = request.app.state.api_key

    if credentials and credentials.credentials == api_key:
        return credentials.credentials

    x_api_key = request.headers.get("x-api-key")
    if x_api_key and x_api_key == api_key:
        return x_api_key

    raise HTTPException(status_code=401, detail="Invalid API key")


def hash_api_key(key: str) -> str:
    """Hash API key for logging."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# === STREAMING HELPER ===
async def stream_sse_chunks(
    orchestrator,
    request: RequestMessage,
    call_id: str,
    span,
) -> AsyncIterator[str]:
    """Stream chunks as SSE format."""
    try:
        async for chunk in orchestrator.process_streaming_response(request, call_id, span):
            # Serialize to SSE format
            if hasattr(chunk, "model_dump_json"):
                sse_line = f"data: {chunk.model_dump_json()}\n\n"
                yield sse_line
            else:
                sse_line = f"data: {json.dumps({'error': 'Unknown chunk type'})}\n\n"
                logger.error(f"[{call_id}] Unknown chunk type: {type(chunk)}")
                yield sse_line

        logger.info(f"[{call_id}] V4 stream complete")

    except Exception as exc:
        logger.error(f"[{call_id}] V4 streaming error: {exc}", exc_info=True)
        error_data = {"error": str(exc), "type": type(exc).__name__}
        yield f"data: {json.dumps(error_data)}\n\n"


# === ROUTES ===


@router.post("/chat/completions")
async def chat_completions_v4(
    request: Request,
    _: str = Depends(verify_token),
):
    """OpenAI-compatible chat completions endpoint using V4 pipeline.

    This endpoint uses the refactored PolicyOrchestrator pipeline.
    """
    # Parse request body
    body = await request.json()
    call_id = str(uuid.uuid4())

    # Get dependencies from app state
    db_pool: db.DatabasePool | None = getattr(request.app.state, "db_pool", None)
    event_publisher: RedisEventPublisher | None = getattr(request.app.state, "event_publisher", None)

    # Use passthrough policy for now (TODO: load from config)
    policy = SimplePolicy()

    # Create LLM client and orchestrator
    llm_client = LiteLLMClient()
    orchestrator = create_default_orchestrator(
        policy=policy,
        llm_client=llm_client,
        db_pool=db_pool,
        event_publisher=event_publisher,
    )

    # Create request message
    request_message = RequestMessage(**body)
    is_streaming = request_message.stream

    logger.info(f"[{call_id}] V4 /chat/completions request: model={request_message.model}, stream={is_streaming}")

    # Start span
    with tracer.start_as_current_span(
        "v4.chat_completions",
        attributes={
            "call_id": call_id,
            "model": request_message.model,
            "stream": is_streaming,
        },
    ) as span:
        # Process request through policy
        final_request = await orchestrator.process_request(request_message, call_id, span)

        if is_streaming:
            # Streaming response
            return FastAPIStreamingResponse(
                stream_sse_chunks(orchestrator, final_request, call_id, span),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Call-ID": call_id,
                },
            )
        else:
            # Non-streaming response
            response = await orchestrator.process_full_response(final_request, call_id, span)
            return JSONResponse(
                content=response.model_dump(),
                headers={"X-Call-ID": call_id},
            )


__all__ = ["router"]
