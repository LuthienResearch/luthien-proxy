"""LLM gateway API routes with PolicyOrchestrator."""

from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry import trace

from luthien_proxy.dependencies import (
    get_api_key,
    get_emitter,
    get_llm_client,
    get_policy,
)
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.llm.llm_format_utils import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)
from luthien_proxy.messages import Request as RequestMessage
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.observability.transaction_recorder import (
    DefaultTransactionRecorder,
)
from luthien_proxy.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.streaming.client_formatter.anthropic import (
    AnthropicClientFormatter,
)
from luthien_proxy.streaming.client_formatter.openai import OpenAIClientFormatter
from luthien_proxy.streaming.policy_executor.executor import PolicyExecutor

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(tags=["gateway"])
security = HTTPBearer(auto_error=False)


# === AUTH ===
def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str = Depends(get_api_key),
) -> str:
    """Verify API key from either Authorization header or x-api-key header."""
    if credentials and credentials.credentials == api_key:
        return credentials.credentials

    x_api_key = request.headers.get("x-api-key")
    if x_api_key and x_api_key == api_key:
        return x_api_key

    raise HTTPException(status_code=401, detail="Invalid API key")


def hash_api_key(key: str) -> str:
    """Hash API key for logging."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# === ROUTES ===


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    _: str = Depends(verify_token),
    policy: PolicyProtocol = Depends(get_policy),
    llm_client: LLMClient = Depends(get_llm_client),
    emitter: EventEmitterProtocol = Depends(get_emitter),
):
    """OpenAI-compatible chat completions endpoint."""
    # Check request size (default 10MB limit)
    if (content_length := request.headers.get("content-length")) and int(content_length) > 10_485_760:
        raise HTTPException(status_code=413, detail="Request payload too large")

    body = await request.json()
    call_id = str(uuid.uuid4())

    # Create request message
    request_message = RequestMessage(**body)
    is_streaming = request_message.stream

    logger.info(f"[{call_id}] /v1/chat/completions: model={request_message.model}, stream={is_streaming}")

    # Get auto-instrumented span (created by FastAPIInstrumentor)
    span = trace.get_current_span()

    # Add custom attributes to auto-instrumented span
    span.set_attribute("luthien.call_id", call_id)
    span.set_attribute("luthien.endpoint", "/v1/chat/completions")
    span.set_attribute("luthien.model", request_message.model)
    span.set_attribute("luthien.stream", is_streaming)

    # Log incoming request
    emitter.record(call_id, "pipeline.client_request", {"payload": body})

    # Create policy context (shared across request/response)
    policy_ctx = PolicyContext(transaction_id=call_id, request=request_message, emitter=emitter)

    # Create pipeline dependencies
    recorder = DefaultTransactionRecorder(transaction_id=call_id, emitter=emitter)
    policy_executor = PolicyExecutor(recorder=recorder)
    client_formatter = OpenAIClientFormatter(model_name=request_message.model)

    # Create orchestrator with injected dependencies
    orchestrator = PolicyOrchestrator(
        policy=policy,
        policy_executor=policy_executor,
        client_formatter=client_formatter,
        transaction_recorder=recorder,
    )

    # Process request through policy
    final_request = await orchestrator.process_request(request_message, policy_ctx)

    # Log request after policy processing
    emitter.record(call_id, "pipeline.backend_request", {"payload": final_request.model_dump(exclude_none=True)})

    # Call backend LLM (llm_client injected via Depends)
    if is_streaming:
        # Get backend stream and process through pipeline
        backend_stream = await llm_client.stream(final_request)

        # Streaming response
        return FastAPIStreamingResponse(
            orchestrator.process_streaming_response(backend_stream, policy_ctx),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Call-ID": call_id,
            },
        )
    else:
        # Non-streaming response
        response = await llm_client.complete(final_request)
        processed_response = await orchestrator.process_full_response(response, policy_ctx)

        # Log final response
        emitter.record(call_id, "pipeline.client_response", {"payload": processed_response.model_dump()})

        return JSONResponse(
            content=processed_response.model_dump(),
            headers={"X-Call-ID": call_id},
        )


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    _: str = Depends(verify_token),
    policy: PolicyProtocol = Depends(get_policy),
    llm_client: LLMClient = Depends(get_llm_client),
    emitter: EventEmitterProtocol = Depends(get_emitter),
):
    """Anthropic Messages API endpoint."""
    # Check request size (default 10MB limit)
    if (content_length := request.headers.get("content-length")) and int(content_length) > 10_485_760:
        raise HTTPException(status_code=413, detail="Request payload too large")

    anthropic_body = await request.json()
    call_id = str(uuid.uuid4())

    # Get auto-instrumented span (created by FastAPIInstrumentor)
    span = trace.get_current_span()

    # Add custom attributes to auto-instrumented span
    span.set_attribute("luthien.call_id", call_id)
    span.set_attribute("luthien.endpoint", "/v1/messages")
    span.set_attribute("luthien.model", anthropic_body.get("model"))

    # Log incoming Anthropic request
    emitter.record(call_id, "pipeline.client_request", {"payload": anthropic_body})

    # Convert Anthropic request to OpenAI format
    logger.info(f"[{call_id}] /v1/messages: Incoming Anthropic request for model={anthropic_body.get('model')}")
    openai_body = anthropic_to_openai_request(anthropic_body)

    # Log format conversion
    emitter.record(
        call_id,
        "pipeline.format_conversion",
        {"from_format": "anthropic", "to_format": "openai", "openai_body": openai_body},
    )

    # Create request message
    request_message = RequestMessage(**openai_body)
    is_streaming = request_message.stream

    logger.info(
        f"[{call_id}] /v1/messages: Converted to OpenAI format, model={request_message.model}, stream={is_streaming}"
    )

    # Update span attributes
    span.set_attribute("luthien.stream", is_streaming)

    # Create policy context (shared across request/response)
    policy_ctx = PolicyContext(transaction_id=call_id, request=request_message, emitter=emitter)

    # Create pipeline dependencies
    recorder = DefaultTransactionRecorder(transaction_id=call_id, emitter=emitter)
    policy_executor = PolicyExecutor(recorder=recorder)
    client_formatter = AnthropicClientFormatter(model_name=request_message.model)

    # Create orchestrator with injected dependencies
    orchestrator = PolicyOrchestrator(
        policy=policy,
        policy_executor=policy_executor,
        client_formatter=client_formatter,
        transaction_recorder=recorder,
    )

    # Process request through policy
    final_request = await orchestrator.process_request(request_message, policy_ctx)

    # Log request after policy processing
    emitter.record(call_id, "pipeline.backend_request", {"payload": final_request.model_dump(exclude_none=True)})

    # Call backend LLM (llm_client injected via Depends)
    if is_streaming:
        # Get backend stream and process through pipeline
        backend_stream = await llm_client.stream(final_request)

        # Streaming response in Anthropic format
        return FastAPIStreamingResponse(
            orchestrator.process_streaming_response(backend_stream, policy_ctx),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Call-ID": call_id,
            },
        )
    else:
        # Non-streaming response
        openai_response = await llm_client.complete(final_request)
        processed_response = await orchestrator.process_full_response(openai_response, policy_ctx)

        # Convert back to Anthropic format
        anthropic_response = openai_to_anthropic_response(processed_response)

        # Log final response
        emitter.record(call_id, "pipeline.client_response", {"payload": anthropic_response})

        return JSONResponse(
            content=anthropic_response,
            headers={"X-Call-ID": call_id},
        )


__all__ = ["router"]
