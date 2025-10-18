# ABOUTME: V2 API routes for OpenAI and Anthropic endpoints with OpenTelemetry tracing
# ABOUTME: Integrated into control plane app as /v2/* routes

"""V2 API routes with OpenTelemetry observability."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator

import litellm
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from opentelemetry import trace

from luthien_proxy.v2.llm.format_converters import (
    anthropic_to_openai_request,
    openai_chunk_to_anthropic_chunk,
    openai_to_anthropic_response,
)
from luthien_proxy.v2.messages import FullResponse
from luthien_proxy.v2.messages import Request as RequestMessage
from luthien_proxy.v2.messages import StreamingResponse as StreamingResponseMessage

if TYPE_CHECKING:
    from luthien_proxy.v2.control.local import ControlPlaneLocal
    from luthien_proxy.v2.observability import SimpleEventPublisher

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(prefix="/v2", tags=["v2"])


# === STREAMING HELPERS ===
async def stream_llm_chunks(data: dict) -> AsyncIterator[StreamingResponseMessage]:
    """Stream chunks from LLM, wrapped in StreamingResponseMessage."""
    response = await litellm.acompletion(**data)
    async for chunk in response:  # type: ignore[attr-defined]
        yield StreamingResponseMessage.from_model_response(chunk)


async def stream_with_policy_control(
    data: dict,
    call_id: str,
    control_plane: ControlPlaneLocal,
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


def hash_api_key(key: str) -> str:
    """Hash API key for logging."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# === ENDPOINTS ===


@router.post("/chat/completions")
async def openai_chat_completions(
    request: Request,
):
    """OpenAI-compatible endpoint with OpenTelemetry tracing."""
    # Get V2 components from app state
    control_plane: ControlPlaneLocal = request.app.state.v2_control_plane
    event_publisher: SimpleEventPublisher | None = request.app.state.v2_event_publisher

    data = await request.json()

    # Generate call_id
    call_id = str(uuid.uuid4())
    trace_id = data.get("metadata", {}).get("trace_id")

    # Create span for the entire request/response cycle
    with tracer.start_as_current_span("gateway.chat_completions") as span:
        # Add span attributes
        span.set_attribute("luthien.call_id", call_id)
        span.set_attribute("luthien.endpoint", "/v2/chat/completions")
        span.set_attribute("luthien.model", data.get("model", "unknown"))
        span.set_attribute("luthien.stream", data.get("stream", False))
        if trace_id:
            span.set_attribute("luthien.trace_id", trace_id)

        # Publish: original request received (for real-time UI)
        if event_publisher:
            await event_publisher.publish_event(
                call_id=call_id,
                event_type="gateway.request_received",
                data={
                    "endpoint": "/v2/chat/completions",
                    "model": data.get("model", "unknown"),
                    "stream": data.get("stream", False),
                },
            )

        # Wrap request data in RequestMessage type
        request_msg = RequestMessage(**data)

        # Apply request policies
        request_msg = await control_plane.process_request(request_msg, call_id)

        # Extract back to dict for LiteLLM
        data = request_msg.model_dump(exclude_none=True)
        is_streaming = data.get("stream", False)

        # Publish: final request being sent to backend (for real-time UI)
        if event_publisher:
            await event_publisher.publish_event(
                call_id=call_id,
                event_type="gateway.request_sent",
                data={
                    "model": data.get("model", "unknown"),
                    "stream": is_streaming,
                },
            )

        # Identify any model-specific parameters to forward
        known_params = {"verbosity"}  # Add more as needed
        model_specific_params = [p for p in data.keys() if p in known_params]
        if model_specific_params:
            data["allowed_openai_params"] = model_specific_params

        try:
            if is_streaming:
                return FastAPIStreamingResponse(
                    stream_with_policy_control(data, call_id, control_plane),
                    media_type="text/event-stream",
                )
            else:
                response = await litellm.acompletion(**data)  # type: ignore[arg-type]

                # Extract response details
                response_dict = response.model_dump() if hasattr(response, "model_dump") else response  # type: ignore[union-attr]
                choices = response_dict.get("choices", [])  # type: ignore[union-attr]
                finish_reason = choices[0].get("finish_reason") if choices else None

                # Publish: original response received (for real-time UI)
                if event_publisher:
                    await event_publisher.publish_event(
                        call_id=call_id,
                        event_type="gateway.response_received",
                        data={
                            "model": str(response_dict.get("model", "unknown")),  # type: ignore[union-attr]
                            "finish_reason": str(finish_reason) if finish_reason else None,
                        },
                    )

                # Wrap in FullResponse and apply policy
                full_response = FullResponse.from_model_response(response)
                full_response = await control_plane.process_full_response(full_response, call_id)

                # Extract final response details
                final_dict = full_response.to_model_response().model_dump()
                final_choices = final_dict.get("choices", [])

                # Publish: final response being sent (for real-time UI)
                if event_publisher:
                    await event_publisher.publish_event(
                        call_id=call_id,
                        event_type="gateway.response_sent",
                        data={
                            "finish_reason": final_choices[0].get("finish_reason") if final_choices else None,
                        },
                    )

                # Extract and return
                return JSONResponse(full_response.to_model_response().model_dump())
        except Exception as exc:
            logger.error(f"Error in chat completion: {exc}")
            span.record_exception(exc)
            span.set_attribute("luthien.error", True)
            raise HTTPException(status_code=500, detail=str(exc))


@router.post("/messages")
async def anthropic_messages(
    request: Request,
):
    """Anthropic Messages API endpoint with OpenTelemetry tracing."""
    # Get V2 components from app state
    control_plane: ControlPlaneLocal = request.app.state.v2_control_plane
    event_publisher: SimpleEventPublisher | None = request.app.state.v2_event_publisher

    anthropic_data = await request.json()
    openai_data = anthropic_to_openai_request(anthropic_data)

    # Generate call_id
    call_id = str(uuid.uuid4())

    # Create span for the entire request/response cycle
    with tracer.start_as_current_span("gateway.anthropic_messages") as span:
        # Add span attributes
        span.set_attribute("luthien.call_id", call_id)
        span.set_attribute("luthien.endpoint", "/v2/messages")
        span.set_attribute("luthien.model", openai_data.get("model", "unknown"))
        span.set_attribute("luthien.stream", openai_data.get("stream", False))

        # Publish: original request received (for real-time UI)
        if event_publisher:
            await event_publisher.publish_event(
                call_id=call_id,
                event_type="gateway.request_received",
                data={
                    "endpoint": "/v2/messages",
                    "model": openai_data.get("model", "unknown"),
                    "stream": openai_data.get("stream", False),
                },
            )

        # Wrap request data in RequestMessage type
        request_msg = RequestMessage(**openai_data)

        # Apply request policies
        request_msg = await control_plane.process_request(request_msg, call_id)

        # Extract back to dict for LiteLLM
        openai_data = request_msg.model_dump(exclude_none=True)
        is_streaming = openai_data.get("stream", False)

        # Identify any model-specific parameters to forward
        known_params = {"verbosity"}  # Add more as needed
        model_specific_params = [p for p in openai_data.keys() if p in known_params]
        if model_specific_params:
            openai_data["allowed_openai_params"] = model_specific_params

        try:
            if is_streaming:
                return FastAPIStreamingResponse(
                    stream_with_policy_control(
                        openai_data, call_id, control_plane, format_converter=openai_chunk_to_anthropic_chunk
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
            logger.error(f"Error in Anthropic messages: {exc}")
            span.record_exception(exc)
            span.set_attribute("luthien.error", True)
            raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
async def health_check():
    """V2 health check endpoint."""
    return {"status": "healthy", "service": "luthien-v2", "version": "2.0.0"}
