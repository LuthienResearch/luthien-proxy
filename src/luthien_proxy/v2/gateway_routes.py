# ABOUTME: LLM gateway routes using PolicyOrchestrator refactored pipeline
# ABOUTME: Handles /v1/chat/completions and /v1/messages with policy control and tracing

"""LLM gateway API routes with PolicyOrchestrator."""

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
from luthien_proxy.v2.llm.format_converters import (
    anthropic_to_openai_request,
    openai_chunk_to_anthropic_chunk,
    openai_to_anthropic_response,
)
from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.messages import Request as RequestMessage
from luthien_proxy.v2.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.v2.orchestration.factory import create_default_orchestrator
from luthien_proxy.v2.policies.simple_policy import SimplePolicy

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(tags=["gateway"])
security = HTTPBearer(auto_error=False)


# === AUTH ===
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


# === STREAMING HELPER FOR OPENAI FORMAT ===
async def stream_openai_sse(
    orchestrator,
    request: RequestMessage,
    call_id: str,
    span,
) -> AsyncIterator[str]:
    """Stream chunks in OpenAI SSE format."""
    try:
        async for chunk in orchestrator.process_streaming_response(request, call_id, span):
            if hasattr(chunk, "model_dump_json"):
                sse_line = f"data: {chunk.model_dump_json()}\n\n"
                yield sse_line
            else:
                logger.error(f"[{call_id}] Unknown chunk type: {type(chunk)}")
                yield f"data: {json.dumps({'error': 'Unknown chunk type'})}\n\n"

        logger.info(f"[{call_id}] Stream complete")

    except Exception as exc:
        logger.error(f"[{call_id}] Streaming error: {exc}", exc_info=True)
        error_data = {"error": str(exc), "type": type(exc).__name__}
        yield f"data: {json.dumps(error_data)}\n\n"


# === STREAMING HELPER FOR ANTHROPIC FORMAT ===
async def stream_anthropic_sse(
    orchestrator,
    request: RequestMessage,
    call_id: str,
    span,
    model_name: str,
) -> AsyncIterator[str]:
    """Stream chunks in Anthropic SSE format."""
    message_started = False

    try:
        async for chunk in orchestrator.process_streaming_response(request, call_id, span):
            # Send message_start before first chunk
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
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
                yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"

            # Convert chunk to Anthropic format
            anthropic_chunk = openai_chunk_to_anthropic_chunk(chunk)

            # Handle complete tool calls
            if isinstance(anthropic_chunk, dict) and anthropic_chunk.get("_complete_tool_call"):
                # Emit start, delta, and stop events for complete tool call
                index = anthropic_chunk.get("index", 0)

                start_chunk = {
                    "type": anthropic_chunk["type"],
                    "index": index,
                    "content_block": anthropic_chunk["content_block"],
                }
                yield f"event: {start_chunk['type']}\ndata: {json.dumps(start_chunk)}\n\n"

                delta_chunk = {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": anthropic_chunk["_arguments"],
                    },
                }
                yield f"event: content_block_delta\ndata: {json.dumps(delta_chunk)}\n\n"

                stop_chunk = {"type": "content_block_stop", "index": index}
                yield f"event: content_block_stop\ndata: {json.dumps(stop_chunk)}\n\n"
            else:
                # Regular chunk
                event_type = (
                    anthropic_chunk.get("type", "content_block_delta")
                    if isinstance(anthropic_chunk, dict)
                    else "content_block_delta"
                )
                yield f"event: {event_type}\ndata: {json.dumps(anthropic_chunk)}\n\n"

        # Send message_stop at end
        message_stop = {"type": "message_stop"}
        yield f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"

        logger.info(f"[{call_id}] Anthropic stream complete")

    except Exception as exc:
        logger.error(f"[{call_id}] Anthropic streaming error: {exc}", exc_info=True)
        error_data = {"error": str(exc), "type": type(exc).__name__}
        yield f"data: {json.dumps(error_data)}\n\n"


# === ROUTES ===


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    _: str = Depends(verify_token),
):
    """OpenAI-compatible chat completions endpoint."""
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

    logger.info(f"[{call_id}] /v1/chat/completions: model={request_message.model}, stream={is_streaming}")

    # Start span
    with tracer.start_as_current_span(
        "gateway.chat_completions",
        attributes={
            "luthien.call_id": call_id,
            "luthien.endpoint": "/v1/chat/completions",
            "luthien.model": request_message.model,
            "luthien.stream": is_streaming,
        },
    ) as span:
        # Process request through policy
        final_request = await orchestrator.process_request(request_message, call_id, span)

        if is_streaming:
            # Streaming response
            return FastAPIStreamingResponse(
                stream_openai_sse(orchestrator, final_request, call_id, span),
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


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    _: str = Depends(verify_token),
):
    """Anthropic Messages API endpoint."""
    anthropic_body = await request.json()
    call_id = str(uuid.uuid4())

    # Get dependencies from app state
    db_pool: db.DatabasePool | None = getattr(request.app.state, "db_pool", None)
    event_publisher: RedisEventPublisher | None = getattr(request.app.state, "event_publisher", None)

    # Convert Anthropic request to OpenAI format
    logger.info(f"[{call_id}] /v1/messages: Incoming Anthropic request for model={anthropic_body.get('model')}")
    openai_body = anthropic_to_openai_request(anthropic_body)

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
    request_message = RequestMessage(**openai_body)
    is_streaming = request_message.stream
    model_name = anthropic_body.get("model", "unknown")

    logger.info(
        f"[{call_id}] /v1/messages: Converted to OpenAI format, model={request_message.model}, stream={is_streaming}"
    )

    # Start span
    with tracer.start_as_current_span(
        "gateway.anthropic_messages",
        attributes={
            "luthien.call_id": call_id,
            "luthien.endpoint": "/v1/messages",
            "luthien.model": request_message.model,
            "luthien.stream": is_streaming,
        },
    ) as span:
        # Process request through policy
        final_request = await orchestrator.process_request(request_message, call_id, span)

        if is_streaming:
            # Streaming response in Anthropic format
            return FastAPIStreamingResponse(
                stream_anthropic_sse(orchestrator, final_request, call_id, span, model_name),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Call-ID": call_id,
                },
            )
        else:
            # Non-streaming response
            openai_response = await orchestrator.process_full_response(final_request, call_id, span)

            # Convert back to Anthropic format
            anthropic_response = openai_to_anthropic_response(openai_response)
            return JSONResponse(
                content=anthropic_response,
                headers={"X-Call-ID": call_id},
            )


__all__ = ["router"]
