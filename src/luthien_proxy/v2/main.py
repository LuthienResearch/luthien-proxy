# ABOUTME: Main FastAPI application for V2 integrated architecture
# ABOUTME: Combines API gateway, control plane, and LLM client with OpenTelemetry tracing

"""Luthien V2 - integrated FastAPI + LiteLLM proxy with OpenTelemetry observability."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import litellm
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace
from redis.asyncio import Redis

from luthien_proxy.v2.activity.events import (
    FinalRequestSent,
    FinalResponseSent,
    OriginalRequestReceived,
    OriginalResponseReceived,
)
from luthien_proxy.v2.activity.publisher import ActivityPublisher
from luthien_proxy.v2.activity.stream import stream_activity_events
from luthien_proxy.v2.control.local import ControlPlaneLocal
from luthien_proxy.v2.llm.format_converters import (
    anthropic_to_openai_request,
    openai_chunk_to_anthropic_chunk,
    openai_to_anthropic_response,
)
from luthien_proxy.v2.messages import FullResponse
from luthien_proxy.v2.messages import Request as RequestMessage
from luthien_proxy.v2.messages import StreamingResponse as StreamingResponseMessage
from luthien_proxy.v2.observability import SimpleEventPublisher
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.noop import NoOpPolicy
from luthien_proxy.v2.telemetry import setup_telemetry

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# === CONFIGURATION ===
API_KEY = os.getenv("PROXY_API_KEY", "")
if not API_KEY:
    raise ValueError("PROXY_API_KEY environment variable required")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Swap out the policy handler here!
POLICY_HANDLER: LuthienPolicy = NoOpPolicy()

# === REDIS & CONTROL PLANE ===
redis_client: Redis | None = None
control_plane: ControlPlaneLocal = None  # type: ignore[assignment]
activity_publisher: ActivityPublisher = None  # type: ignore[assignment]
event_publisher: SimpleEventPublisher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan: startup and shutdown."""
    global redis_client, control_plane, activity_publisher, event_publisher

    # Startup
    logger.info("Starting Luthien V2 Gateway...")

    # Initialize OpenTelemetry
    setup_telemetry(app)
    logger.info("OpenTelemetry initialized")

    # Connect to Redis
    try:
        redis_client = Redis.from_url(REDIS_URL, decode_responses=False)
        await redis_client.ping()
        logger.info(f"Connected to Redis at {REDIS_URL}")
    except Exception as exc:
        logger.warning(f"Failed to connect to Redis: {exc}. Activity stream will be disabled.")
        redis_client = None

    # Initialize activity publisher (legacy - for Phase 8 cleanup)
    activity_publisher = ActivityPublisher(redis_client)

    # Initialize event publisher for real-time UI
    if redis_client:
        event_publisher = SimpleEventPublisher(redis_client)
        logger.info("Event publisher initialized for real-time UI")
    else:
        event_publisher = None
        logger.info("Event publisher disabled (no Redis)")

    # Initialize control plane with event publisher
    control_plane = ControlPlaneLocal(
        policy=POLICY_HANDLER,
        event_publisher=event_publisher,
    )
    logger.info("Control plane initialized with OpenTelemetry tracing")

    yield

    # Shutdown
    if redis_client:
        await redis_client.close()
        logger.info("Closed Redis connection")


# === APP SETUP ===
app = FastAPI(
    title="Luthien V2 Proxy Gateway",
    description="Multi-provider LLM proxy with integrated control plane",
    version="2.0.0",
    lifespan=lifespan,
)
security = HTTPBearer()

# Mount static files for activity monitor UI
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/v2/static", StaticFiles(directory=STATIC_DIR), name="static")


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
    call_id: str,
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
        policy_stream = control_plane.process_streaming_response(llm_stream, call_id)

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

    # Generate call_id
    call_id = str(uuid.uuid4())
    trace_id = data.get("metadata", {}).get("trace_id")
    api_key_hash = hash_api_key(token)

    # Create span for the entire request/response cycle
    with tracer.start_as_current_span("gateway.chat_completions") as span:
        # Add span attributes
        span.set_attribute("luthien.call_id", call_id)
        span.set_attribute("luthien.endpoint", "/v1/chat/completions")
        span.set_attribute("luthien.model", data.get("model", "unknown"))
        span.set_attribute("luthien.stream", data.get("stream", False))
        if trace_id:
            span.set_attribute("luthien.trace_id", trace_id)

        # Publish: original request received
        await activity_publisher.publish(
            OriginalRequestReceived(
                call_id=call_id,
                trace_id=trace_id,
                endpoint="/v1/chat/completions",
                model=data.get("model", "unknown"),
                messages=data.get("messages", []),
                stream=data.get("stream", False),
                api_key_hash=api_key_hash,
                metadata=data.get("metadata", {}),
            )
        )

        # Wrap request data in RequestMessage type
        request_msg = RequestMessage(**data)

        # Apply request policies
        request_msg = await control_plane.process_request(request_msg, call_id)

        # Extract back to dict for LiteLLM
        data = request_msg.model_dump(exclude_none=True)
        is_streaming = data.get("stream", False)

        # Publish: final request being sent to backend
        await activity_publisher.publish(
            FinalRequestSent(
                call_id=call_id,
                trace_id=trace_id,
                model=data.get("model", "unknown"),
                messages=data.get("messages", []),
                stream=is_streaming,
                modifications=[],  # TODO: Track modifications from policy
            )
        )

        # Identify any model-specific parameters to forward
        # (litellm will pass these through to the underlying provider)
        known_params = {"verbosity"}  # Add more as needed
        model_specific_params = [p for p in data.keys() if p in known_params]
        if model_specific_params:
            data["allowed_openai_params"] = model_specific_params

        try:
            if is_streaming:
                return StreamingResponse(
                    stream_with_policy_control(data, call_id),
                    media_type="text/event-stream",
                )
            else:
                response = await litellm.acompletion(**data)  # type: ignore[arg-type]

                # Publish: original response received from backend
                response_dict = response.model_dump() if hasattr(response, "model_dump") else response  # type: ignore[union-attr]
                choices = response_dict.get("choices", [])  # type: ignore[union-attr]
                content = choices[0].get("message", {}).get("content", "") if choices else ""
                usage = response_dict.get("usage")  # type: ignore[union-attr]
                finish_reason = choices[0].get("finish_reason") if choices else None

                await activity_publisher.publish(
                    OriginalResponseReceived(
                        call_id=call_id,
                        trace_id=trace_id,
                        model=str(response_dict.get("model", "unknown")),  # type: ignore[union-attr]
                        content=str(content),
                        usage=usage,
                        finish_reason=str(finish_reason) if finish_reason else None,
                    )
                )

                # Wrap in FullResponse and apply policy
                full_response = FullResponse.from_model_response(response)
                full_response = await control_plane.process_full_response(full_response, call_id)

                # Publish: final response being sent to client
                final_dict = full_response.to_model_response().model_dump()
                final_choices = final_dict.get("choices", [])
                final_content = final_choices[0].get("message", {}).get("content", "") if final_choices else ""

                await activity_publisher.publish(
                    FinalResponseSent(
                        call_id=call_id,
                        trace_id=trace_id,
                        content=final_content,
                        usage=final_dict.get("usage"),
                        finish_reason=final_choices[0].get("finish_reason") if final_choices else None,
                        modifications=[],  # TODO: Track modifications from policy
                    )
                )

                # Extract and return
                return JSONResponse(full_response.to_model_response().model_dump())
        except Exception as exc:
            logger.error(f"Error in chat completion: {exc}")
            span.record_exception(exc)
            span.set_attribute("luthien.error", True)
            raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    token: str = Security(verify_token),
):
    """Anthropic Messages API endpoint."""
    anthropic_data = await request.json()
    openai_data = anthropic_to_openai_request(anthropic_data)

    # Generate call_id
    call_id = str(uuid.uuid4())

    # Wrap request data in RequestMessage type
    request_msg = RequestMessage(**openai_data)

    # Apply request policies
    request_msg = await control_plane.process_request(request_msg, call_id)

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
                    call_id,
                    format_converter=openai_chunk_to_anthropic_chunk,
                ),
                media_type="text/event-stream",
            )
        else:
            response = await litellm.acompletion(**openai_data)  # type: ignore[arg-type]

            # Wrap in FullResponse and apply policy
            full_response = FullResponse.from_model_response(response)
            full_response = await control_plane.process_full_response(full_response, call_id)

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
            "activity_stream": "/v2/activity/stream",
        },
    }


@app.get("/v2/activity/stream")
async def activity_stream():
    """Server-Sent Events stream of activity events.

    This endpoint streams all V2 gateway activity in real-time for debugging.
    Events include: request received, policy events, responses sent, etc.

    Returns:
        StreamingResponse with Server-Sent Events (text/event-stream)
    """
    if not redis_client:
        raise HTTPException(
            status_code=503,
            detail="Activity stream unavailable (Redis not connected)",
        )

    return StreamingResponse(
        stream_activity_events(redis_client),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@app.get("/v2/activity/monitor")
async def activity_monitor():
    """Activity monitor UI.

    Returns the HTML page for viewing the activity stream in real-time.
    """
    return FileResponse(os.path.join(STATIC_DIR, "activity_monitor.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
