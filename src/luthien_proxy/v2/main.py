# ABOUTME: Main FastAPI application for V2 integrated architecture
# ABOUTME: Combines API gateway, control plane, and LLM client in single process

"""Luthien V2 - integrated FastAPI + LiteLLM proxy with network-ready control plane."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import litellm
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.v2.control.local import ControlPlaneLocal
from luthien_proxy.v2.control.models import RequestMetadata
from luthien_proxy.v2.llm.format_converters import (
    anthropic_to_openai_request,
    openai_chunk_to_anthropic_chunk,
    openai_to_anthropic_response,
)
from luthien_proxy.v2.messages import FullResponse
from luthien_proxy.v2.messages import Request as RequestMessage
from luthien_proxy.v2.messages import StreamingResponse as StreamingResponseMessage
from luthien_proxy.v2.policies.base import PolicyHandler
from luthien_proxy.v2.policies.noop import NoOpPolicy

logger = logging.getLogger(__name__)

# === CONFIGURATION ===
API_KEY = os.getenv("PROXY_API_KEY", "")
if not API_KEY:
    raise ValueError("PROXY_API_KEY environment variable required")

# Swap out the policy handler here!
POLICY_HANDLER: PolicyHandler = NoOpPolicy()

# === APP SETUP ===
app = FastAPI(
    title="Luthien V2 Proxy Gateway",
    description="Multi-provider LLM proxy with integrated control plane",
    version="2.0.0",
)
security = HTTPBearer()

# === CONTROL PLANE ===
# In Phase 1, this is local. In Phase 2, could be ControlPlaneHTTP
control_plane = ControlPlaneLocal(
    policy=POLICY_HANDLER,
    db_pool=None,  # TODO: Initialize database pool
    redis_client=None,  # TODO: Initialize Redis client
)


# === AUTH ===
async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """Verify API key and return it."""
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials


def hash_api_key(key: str) -> str:
    """Hash API key for logging."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# === STREAMING HELPERS ===
async def stream_llm_chunks(data: dict) -> AsyncIterator[StreamingResponseMessage]:
    """Stream chunks from LLM, wrapped in StreamingResponseMessage."""
    response = await litellm.acompletion(**data)
    async for chunk in response:  # type: ignore[attr-defined]
        yield StreamingResponseMessage.from_model_response(chunk)


async def stream_with_policy_control(
    data: dict,
    metadata: RequestMetadata,
    format_converter=None,
) -> AsyncIterator[str]:
    """Stream with reactive policy control.

    This creates an async iterator from LLM, passes it through the control plane's
    process_streaming_response, and yields formatted chunks to the client.
    """
    try:
        # Create async iterator of StreamingResponse objects from LLM
        llm_stream = stream_llm_chunks(data)

        # Process through control plane (applies policy via queue-based reactive processing)
        policy_stream = control_plane.process_streaming_response(llm_stream, metadata)

        # Yield formatted chunks to client
        async for streaming_response in policy_stream:
            # Extract the underlying chunk
            chunk = streaming_response.to_model_response()

            # Apply format conversion if needed
            if format_converter:
                chunk = format_converter(chunk)

            # Serialize to SSE format
            if isinstance(chunk, dict):
                yield f"data: {json.dumps(chunk)}\n\n"
            elif hasattr(chunk, "model_dump_json"):
                yield f"data: {chunk.model_dump_json()}\n\n"
            else:
                yield f"data: {json.dumps({'error': 'Unknown chunk type'})}\n\n"

    except Exception as exc:
        logger.error(f"Streaming error: {exc}")
        error_data = {"error": str(exc), "type": type(exc).__name__}
        yield f"data: {json.dumps(error_data)}\n\n"


# === ENDPOINTS ===


@app.post("/v1/chat/completions")
async def openai_chat_completions(
    request: Request,
    token: str = Security(verify_token),
):
    """OpenAI-compatible endpoint."""
    data = await request.json()

    # Create metadata
    metadata = RequestMetadata(
        call_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        api_key_hash=hash_api_key(token),
        trace_id=data.get("metadata", {}).get("trace_id"),
        user_id=data.get("metadata", {}).get("user_id"),
    )

    # Wrap request data in RequestMessage type
    request_msg = RequestMessage(**data)

    # Apply request policies
    request_msg = await control_plane.process_request(request_msg, metadata)

    # Extract back to dict for LiteLLM
    data = request_msg.model_dump(exclude_none=True)
    is_streaming = data.get("stream", False)

    # Identify any model-specific parameters to forward
    # (litellm will pass these through to the underlying provider)
    known_params = {"verbosity"}  # Add more as needed
    model_specific_params = [p for p in data.keys() if p in known_params]
    if model_specific_params:
        data["allowed_openai_params"] = model_specific_params

    try:
        if is_streaming:
            return StreamingResponse(
                stream_with_policy_control(data, metadata),
                media_type="text/event-stream",
            )
        else:
            response = await litellm.acompletion(**data)  # type: ignore[arg-type]

            # Wrap in FullResponse and apply policy
            full_response = FullResponse.from_model_response(response)
            full_response = await control_plane.process_full_response(full_response, metadata)

            # Extract and return
            return JSONResponse(full_response.to_model_response().model_dump())
    except Exception as exc:
        logger.error(f"Error in chat completion: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    token: str = Security(verify_token),
):
    """Anthropic Messages API endpoint."""
    anthropic_data = await request.json()
    openai_data = anthropic_to_openai_request(anthropic_data)

    # Create metadata
    metadata = RequestMetadata(
        call_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        api_key_hash=hash_api_key(token),
    )

    # Wrap request data in RequestMessage type
    request_msg = RequestMessage(**openai_data)

    # Apply request policies
    request_msg = await control_plane.process_request(request_msg, metadata)

    # Extract back to dict for LiteLLM
    openai_data = request_msg.model_dump(exclude_none=True)
    is_streaming = openai_data.get("stream", False)

    # Identify any model-specific parameters to forward
    # (litellm will pass these through to the underlying provider)
    known_params = {"verbosity"}  # Add more as needed
    model_specific_params = [p for p in openai_data.keys() if p in known_params]
    if model_specific_params:
        openai_data["allowed_openai_params"] = model_specific_params

    try:
        if is_streaming:
            return StreamingResponse(
                stream_with_policy_control(
                    openai_data,
                    metadata,
                    format_converter=openai_chunk_to_anthropic_chunk,
                ),
                media_type="text/event-stream",
            )
        else:
            response = await litellm.acompletion(**openai_data)  # type: ignore[arg-type]

            # Wrap in FullResponse and apply policy
            full_response = FullResponse.from_model_response(response)
            full_response = await control_plane.process_full_response(full_response, metadata)

            # Convert to Anthropic format
            anthropic_response = openai_to_anthropic_response(full_response.to_model_response())
            return JSONResponse(anthropic_response)
    except Exception as exc:
        logger.error(f"Error in messages endpoint: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "version": "2.0.0"}


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Luthien V2 Proxy Gateway",
        "version": "2.0.0",
        "endpoints": {
            "openai": "/v1/chat/completions",
            "anthropic": "/v1/messages",
            "health": "/health",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
