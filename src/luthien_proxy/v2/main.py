# ABOUTME: Main FastAPI application for V2 integrated architecture
# ABOUTME: Combines API gateway, control plane, and LLM client in single process

"""Luthien V2 - integrated FastAPI + LiteLLM proxy with network-ready control plane."""

from __future__ import annotations

import asyncio
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
async def stream_with_bidirectional_control(
    data: dict,
    metadata: RequestMetadata,
    format_converter=None,
) -> AsyncIterator[str]:
    """Bidirectional streaming with full policy control.

    Upstream task reads from LLM and applies policies.
    Downstream yields to client from queue.
    """
    # Create streaming context
    context = await control_plane.create_streaming_context(data, metadata)

    outgoing_queue: asyncio.Queue = asyncio.Queue()
    upstream_task = None

    async def consume_upstream_stream(request_data: dict):
        """Read from upstream LLM and apply policies."""
        try:
            response = await litellm.acompletion(**request_data)
            async for chunk in response:  # type: ignore[attr-defined]
                # Process through control plane
                async for outgoing_chunk in control_plane.process_streaming_chunk(chunk, context):
                    await outgoing_queue.put(outgoing_chunk)

                # Check if we should abort
                if context.should_abort:
                    logger.info("Aborting stream per policy decision")
                    break

        except Exception as exc:
            logger.error(f"Upstream error: {exc}")
            # Put error chunk in queue
            await outgoing_queue.put({"error": str(exc)})
        finally:
            await outgoing_queue.put(None)

    async def produce_downstream():
        """Read from queue and yield to client."""
        while True:
            chunk = await outgoing_queue.get()
            if chunk is None:
                break

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

    upstream_task = asyncio.create_task(consume_upstream_stream(data))

    try:
        async for item in produce_downstream():
            yield item
    finally:
        if upstream_task and not upstream_task.done():
            upstream_task.cancel()
            try:
                await upstream_task
            except asyncio.CancelledError:
                pass


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

    # Apply request policies
    data = await control_plane.apply_request_policies(data, metadata)
    is_streaming = data.get("stream", False)

    try:
        if is_streaming:
            return StreamingResponse(
                stream_with_bidirectional_control(data, metadata),
                media_type="text/event-stream",
            )
        else:
            response = await litellm.acompletion(**data)  # type: ignore[arg-type]

            # Apply response policy
            response = await control_plane.apply_response_policy(response, metadata)  # type: ignore[arg-type]

            return JSONResponse(response.model_dump())
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

    # Apply request policies
    openai_data = await control_plane.apply_request_policies(openai_data, metadata)
    is_streaming = openai_data.get("stream", False)

    try:
        if is_streaming:
            return StreamingResponse(
                stream_with_bidirectional_control(
                    openai_data,
                    metadata,
                    format_converter=openai_chunk_to_anthropic_chunk,
                ),
                media_type="text/event-stream",
            )
        else:
            response = await litellm.acompletion(**openai_data)  # type: ignore[arg-type]

            # Apply response policy
            response = await control_plane.apply_response_policy(response, metadata)  # type: ignore[arg-type]

            anthropic_response = openai_to_anthropic_response(response)
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
