# ABOUTME: LLM gateway routes for OpenAI and Anthropic API endpoints
# ABOUTME: Handles /v1/chat/completions and /v1/messages with policy control and tracing

"""LLM gateway API routes with policy control and OpenTelemetry tracing."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import AsyncIterator, cast

import litellm
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from litellm.types.utils import ModelResponse
from opentelemetry import trace
from redis.asyncio import Redis

from luthien_proxy.utils import db
from luthien_proxy.v2.control.synchronous_control_plane import SynchronousControlPlane
from luthien_proxy.v2.llm.format_converters import (
    anthropic_to_openai_request,
    openai_chunk_to_anthropic_chunk,
    openai_to_anthropic_response,
)
from luthien_proxy.v2.messages import Request as RequestMessage
from luthien_proxy.v2.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.v2.storage import emit_request_event, emit_response_event

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(tags=["gateway"])
security = HTTPBearer(auto_error=False)


# === AUTH ===
def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Verify API key from either Authorization header or x-api-key header.

    Supports both:
    - Authorization: Bearer <key> (OpenAI-style)
    - x-api-key: <key> (Anthropic-style)
    """
    api_key = request.app.state.api_key

    # Try Authorization: Bearer header first
    if credentials and credentials.credentials == api_key:
        return credentials.credentials

    # Try x-api-key header (Anthropic convention)
    x_api_key = request.headers.get("x-api-key")
    if x_api_key and x_api_key == api_key:
        return x_api_key

    raise HTTPException(status_code=401, detail="Invalid API key")


def hash_api_key(key: str) -> str:
    """Hash API key for logging."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# === STREAMING HELPERS ===
async def stream_llm_chunks(data: dict) -> AsyncIterator:
    """Stream chunks from LLM as ModelResponse objects."""
    response = await litellm.acompletion(**data)
    async for chunk in response:  # type: ignore[attr-defined]
        yield chunk


async def stream_with_policy_control(
    data: dict,
    call_id: str,
    control_plane: SynchronousControlPlane,
    db_pool: db.DatabasePool | None,
    redis_client: Redis | None,
    format_converter=None,
) -> AsyncIterator[str]:
    """Stream with reactive policy control.

    This creates an async iterator from LLM, passes it through the control plane's
    process_streaming_response, and yields formatted chunks to the client.
    """
    try:
        # Create async iterator of ModelResponse chunks from LLM
        llm_stream = stream_llm_chunks(data)

        # Process through control plane (applies policy via queue-based reactive processing)
        policy_stream = control_plane.process_streaming_response(
            llm_stream, call_id, db_pool=db_pool, redis_conn=redis_client
        )

        # For Anthropic format, track if we've sent message_start
        message_started = False
        model_name = data.get("model", "unknown")

        # Debug logging
        logger.info(f"[{call_id}] Starting stream with format_converter={format_converter is not None}")

        # Yield formatted chunks to client
        async for chunk in policy_stream:
            # Log raw chunk before conversion
            logger.info(f"[{call_id}] Raw chunk from policy: {chunk}")

            # Apply format conversion if needed
            if format_converter:
                # Send message_start before first chunk (for Anthropic format)
                if not message_started:
                    message_started = True
                    message_start = {
                        "type": "message_start",
                        "message": {
                            "id": f"msg_{call_id}",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model_name,
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                            },
                        },
                    }
                    sse_line = f"event: message_start\ndata: {json.dumps(message_start)}\n\n"
                    logger.info(f"[{call_id}] Sending message_start: {sse_line.strip()}")
                    yield sse_line

                chunk = format_converter(chunk)
                logger.info(f"[{call_id}] Converted chunk: {chunk}")

            # Serialize to SSE format
            if isinstance(chunk, dict):
                # Handle complete tool call (buffered by policy)
                if chunk.get("_complete_tool_call"):
                    logger.info(f"[{call_id}] Handling complete tool call")
                    # Emit start event
                    index = chunk.get("index", 0)
                    start_chunk = {
                        "type": chunk["type"],
                        "index": index,
                        "content_block": chunk["content_block"],
                    }
                    sse_line = f"event: {start_chunk['type']}\ndata: {json.dumps(start_chunk)}\n\n"
                    logger.info(f"[{call_id}] Sending tool start: {sse_line.strip()}")
                    yield sse_line

                    # Emit delta event with full arguments
                    delta_chunk = {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": chunk["_arguments"],
                        },
                    }
                    sse_line = f"event: content_block_delta\ndata: {json.dumps(delta_chunk)}\n\n"
                    logger.info(f"[{call_id}] Sending tool delta: {sse_line.strip()}")
                    yield sse_line

                    # Emit stop event
                    stop_chunk = {
                        "type": "content_block_stop",
                        "index": index,
                    }
                    sse_line = f"event: content_block_stop\ndata: {json.dumps(stop_chunk)}\n\n"
                    logger.info(f"[{call_id}] Sending tool stop: {sse_line.strip()}")
                    yield sse_line
                else:
                    # Regular chunk - include event type in SSE
                    event_type = chunk.get("type", "content_block_delta")
                    sse_line = f"event: {event_type}\ndata: {json.dumps(chunk)}\n\n"
                    logger.info(f"[{call_id}] Sending regular chunk: {sse_line.strip()}")
                    yield sse_line
            elif hasattr(chunk, "model_dump_json"):
                sse_line = f"data: {chunk.model_dump_json()}\n\n"
                logger.info(f"[{call_id}] Sending ModelResponse chunk: {sse_line.strip()}")
                yield sse_line
            else:
                sse_line = f"data: {json.dumps({'error': 'Unknown chunk type'})}\n\n"
                logger.error(f"[{call_id}] Unknown chunk type: {type(chunk)}")
                yield sse_line

        # Send message_stop at the end (for Anthropic format)
        if format_converter:
            message_stop = {"type": "message_stop"}
            sse_line = f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"
            logger.info(f"[{call_id}] Sending message_stop: {sse_line.strip()}")
            yield sse_line

        logger.info(f"[{call_id}] Stream complete")

    except Exception as exc:
        logger.error(f"Streaming error: {exc}")
        error_data = {"error": str(exc), "type": type(exc).__name__}
        yield f"data: {json.dumps(error_data)}\n\n"


# === REQUEST PROCESSING HELPERS ===


async def process_request_with_policy(
    request_data: dict,
    call_id: str,
    control_plane: SynchronousControlPlane,
    db_pool: db.DatabasePool | None,
) -> RequestMessage:
    """Apply policy to request and emit request event.

    Returns the final request after policy processing.
    """
    # Wrap request data in RequestMessage type
    original_request = RequestMessage(**request_data)

    # Apply request policies
    final_request = await control_plane.process_request(original_request, call_id)

    # Emit request event (non-blocking, queued for background persistence)
    emit_request_event(
        call_id=call_id,
        original_request=original_request.model_dump(exclude_none=True),
        final_request=final_request.model_dump(exclude_none=True),
        db_pool=db_pool,
        redis_conn=None,  # Redis events handled separately via event_publisher
    )

    return final_request


async def publish_request_received_event(
    event_publisher: RedisEventPublisher | None,
    call_id: str,
    endpoint: str,
    model: str,
    stream: bool,
) -> None:
    """Publish request received event for real-time UI."""
    if event_publisher:
        await event_publisher.publish_event(
            call_id=call_id,
            event_type="gateway.request_received",
            data={
                "call_id": call_id,
                "endpoint": endpoint,
                "model": model,
                "stream": stream,
            },
        )


async def publish_request_sent_event(
    event_publisher: RedisEventPublisher | None,
    call_id: str,
    model: str,
    stream: bool,
) -> None:
    """Publish request sent event for real-time UI."""
    if event_publisher:
        await event_publisher.publish_event(
            call_id=call_id,
            event_type="gateway.request_sent",
            data={
                "call_id": call_id,
                "model": model,
                "stream": stream,
            },
        )


def add_model_specific_params(data: dict, known_params: set[str]) -> dict:
    """Identify and mark model-specific parameters for LiteLLM forwarding.

    Returns the data dict with allowed_openai_params added if needed.
    """
    model_specific_params = [p for p in data.keys() if p in known_params]
    if model_specific_params:
        data["allowed_openai_params"] = model_specific_params
    return data


# === RESPONSE PROCESSING HELPERS ===


async def process_non_streaming_response(
    data: dict,
    call_id: str,
    control_plane: SynchronousControlPlane,
    db_pool: db.DatabasePool | None,
    event_publisher: RedisEventPublisher | None,
) -> ModelResponse:
    """Process a non-streaming LLM response through policy control.

    Returns the final response after policy processing.
    """
    # Call LiteLLM
    raw_response = await litellm.acompletion(**data)  # type: ignore[arg-type]
    # When stream=False, response is always ModelResponse
    response = cast(ModelResponse, raw_response)

    # Extract response details
    response_dict = response.model_dump()
    choices = response_dict.get("choices", [])
    finish_reason = choices[0].get("finish_reason") if choices else None

    # Publish: original response received (for real-time UI)
    if event_publisher:
        await event_publisher.publish_event(
            call_id=call_id,
            event_type="gateway.response_received",
            data={
                "call_id": call_id,
                "model": str(response_dict.get("model", "unknown")),
                "finish_reason": str(finish_reason) if finish_reason else None,
            },
        )

    # Apply policy to response
    final_response = await control_plane.process_full_response(response, call_id)

    # Emit response event (non-blocking, queued for background persistence)
    emit_response_event(
        call_id=call_id,
        original_response=response.model_dump(),
        final_response=final_response.model_dump(),
        db_pool=db_pool,
        redis_conn=None,  # Already using event_publisher for Redis
    )

    # Extract final response details
    final_dict = final_response.model_dump()
    final_choices = final_dict.get("choices", [])

    # Publish: final response being sent (for real-time UI)
    if event_publisher:
        await event_publisher.publish_event(
            call_id=call_id,
            event_type="gateway.response_sent",
            data={
                "call_id": call_id,
                "finish_reason": final_choices[0].get("finish_reason") if final_choices else None,
            },
        )

    return final_response


# === ENDPOINTS ===


@router.post("/v1/chat/completions")
async def openai_chat_completions(
    request: Request,
    token: str = Depends(verify_token),
):
    """OpenAI-compatible endpoint."""
    # Get dependencies from app state
    control_plane: SynchronousControlPlane = request.app.state.control_plane
    db_pool: db.DatabasePool | None = request.app.state.db_pool
    event_publisher: RedisEventPublisher | None = request.app.state.event_publisher
    redis_client: Redis | None = request.app.state.redis_client

    data = await request.json()

    # Generate call_id
    call_id = str(uuid.uuid4())
    trace_id = data.get("metadata", {}).get("trace_id")

    # Create span for the entire request/response cycle
    with tracer.start_as_current_span("gateway.chat_completions") as span:
        # Add span attributes
        span.set_attribute("luthien.call_id", call_id)
        span.set_attribute("luthien.endpoint", "/v1/chat/completions")
        span.set_attribute("luthien.model", data.get("model", "unknown"))
        span.set_attribute("luthien.stream", data.get("stream", False))
        if trace_id:
            span.set_attribute("luthien.trace_id", trace_id)

        # Publish: original request received (for real-time UI)
        await publish_request_received_event(
            event_publisher,
            call_id,
            "/v1/chat/completions",
            data.get("model", "unknown"),
            data.get("stream", False),
        )

        # Apply request policies and emit request event
        final_request = await process_request_with_policy(data, call_id, control_plane, db_pool)

        # Extract back to dict for LiteLLM
        data = final_request.model_dump(exclude_none=True)
        is_streaming = data.get("stream", False)

        # Publish: final request being sent to backend (for real-time UI)
        await publish_request_sent_event(
            event_publisher,
            call_id,
            data.get("model", "unknown"),
            is_streaming,
        )

        # Identify any model-specific parameters to forward
        known_params = {"verbosity"}  # Add more as needed
        data = add_model_specific_params(data, known_params)

        try:
            if is_streaming:
                return FastAPIStreamingResponse(
                    stream_with_policy_control(data, call_id, control_plane, db_pool, redis_client),
                    media_type="text/event-stream",
                )
            else:
                final_response = await process_non_streaming_response(
                    data, call_id, control_plane, db_pool, event_publisher
                )
                return JSONResponse(final_response.model_dump())
        except Exception as exc:
            logger.error(f"Error in chat completion: {exc}")
            span.record_exception(exc)
            span.set_attribute("luthien.error", True)
            raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    token: str = Depends(verify_token),
):
    """Anthropic Messages API endpoint."""
    # Get dependencies from app state
    control_plane: SynchronousControlPlane = request.app.state.control_plane
    db_pool: db.DatabasePool | None = request.app.state.db_pool
    redis_client: Redis | None = request.app.state.redis_client

    anthropic_data = await request.json()

    # Debug: log the incoming request
    logger.info(f"[/v1/messages] Incoming request: {json.dumps(anthropic_data, indent=2)}")
    openai_data = anthropic_to_openai_request(anthropic_data)

    # Generate call_id
    call_id = str(uuid.uuid4())

    # Create span for the entire request/response cycle
    with tracer.start_as_current_span("gateway.anthropic_messages") as span:
        # Add span attributes
        span.set_attribute("luthien.call_id", call_id)
        span.set_attribute("luthien.endpoint", "/v1/messages")
        span.set_attribute("luthien.model", openai_data.get("model", "unknown"))
        span.set_attribute("luthien.stream", openai_data.get("stream", False))

        # Apply request policies and emit request event
        final_request = await process_request_with_policy(openai_data, call_id, control_plane, db_pool)

        # Extract back to dict for LiteLLM
        openai_data = final_request.model_dump(exclude_none=True)
        is_streaming = openai_data.get("stream", False)

        # Identify any model-specific parameters to forward
        known_params = {"verbosity"}  # Add more as needed
        openai_data = add_model_specific_params(openai_data, known_params)

        try:
            if is_streaming:
                return FastAPIStreamingResponse(
                    stream_with_policy_control(
                        openai_data,
                        call_id,
                        control_plane,
                        db_pool,
                        redis_client,
                        format_converter=openai_chunk_to_anthropic_chunk,
                    ),
                    media_type="text/event-stream",
                )
            else:
                raw_response = await litellm.acompletion(**openai_data)  # type: ignore[arg-type]
                # When stream=False, response is always ModelResponse
                response = cast(ModelResponse, raw_response)

                # Apply policy to response
                final_response = await control_plane.process_full_response(response, call_id)

                # Emit response event (non-blocking, queued for background persistence)
                emit_response_event(
                    call_id=call_id,
                    original_response=response.model_dump(),
                    final_response=final_response.model_dump(),
                    db_pool=db_pool,
                    redis_conn=None,  # Already using event_publisher for Redis
                )

                # Convert to Anthropic format
                anthropic_response = openai_to_anthropic_response(final_response)
                return JSONResponse(anthropic_response)
        except Exception as exc:
            logger.error(f"Error in messages endpoint: {exc}")
            span.record_exception(exc)
            span.set_attribute("luthien.error", True)
            raise HTTPException(status_code=500, detail=str(exc))


__all__ = [
    "router",
    "hash_api_key",
    "verify_token",
    "stream_llm_chunks",
    "stream_with_policy_control",
    "process_request_with_policy",
    "publish_request_received_event",
    "publish_request_sent_event",
    "add_model_specific_params",
    "process_non_streaming_response",
]
