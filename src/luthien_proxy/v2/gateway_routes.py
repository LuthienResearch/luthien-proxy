# ABOUTME: LLM gateway routes using PolicyOrchestrator refactored pipeline
# ABOUTME: Handles /v1/chat/completions and /v1/messages with policy control and tracing

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

from luthien_proxy.utils import db
from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.llm.llm_format_utils import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)
from luthien_proxy.v2.messages import Request as RequestMessage
from luthien_proxy.v2.observability.context import DefaultObservabilityContext
from luthien_proxy.v2.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.v2.observability.transaction_recorder import (
    DefaultTransactionRecorder,
)
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.v2.policies.policy import PolicyProtocol
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.streaming.client_formatter.anthropic import (
    AnthropicClientFormatter,
)
from luthien_proxy.v2.streaming.client_formatter.openai import OpenAIClientFormatter
from luthien_proxy.v2.streaming.policy_executor.executor import PolicyExecutor

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
    if not hasattr(request.app.state, "policy"):
        raise HTTPException(status_code=500, detail="Policy not configured in application state")
    policy: PolicyProtocol = request.app.state.policy

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
        # Create observability context
        obs_ctx = DefaultObservabilityContext(
            transaction_id=call_id,
            span=span,
            db_pool=db_pool,
            event_publisher=event_publisher,
        )

        # Create policy context (shared across request/response)
        policy_ctx = PolicyContext(transaction_id=call_id, request=request_message, observability=obs_ctx)

        # Create pipeline dependencies
        recorder = DefaultTransactionRecorder(observability=obs_ctx)
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
        final_request = await orchestrator.process_request(request_message, policy_ctx, obs_ctx)

        # Record original and final request

        # Call backend LLM
        llm_client = LiteLLMClient()

        if is_streaming:
            # Get backend stream and process through pipeline
            backend_stream = await llm_client.stream(final_request)

            # Streaming response
            return FastAPIStreamingResponse(
                orchestrator.process_streaming_response(backend_stream, policy_ctx, obs_ctx),
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

            return JSONResponse(
                content=processed_response.model_dump(),
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
    if not hasattr(request.app.state, "policy"):
        raise HTTPException(status_code=500, detail="Policy not configured in application state")
    policy: PolicyProtocol = request.app.state.policy

    # Convert Anthropic request to OpenAI format
    logger.info(f"[{call_id}] /v1/messages: Incoming Anthropic request for model={anthropic_body.get('model')}")
    openai_body = anthropic_to_openai_request(anthropic_body)

    # Create request message
    request_message = RequestMessage(**openai_body)
    is_streaming = request_message.stream

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
        # Create observability context
        obs_ctx = DefaultObservabilityContext(
            transaction_id=call_id,
            span=span,
            db_pool=db_pool,
            event_publisher=event_publisher,
        )

        # Create policy context (shared across request/response)
        policy_ctx = PolicyContext(transaction_id=call_id, request=request_message, observability=obs_ctx)

        # Create pipeline dependencies
        recorder = DefaultTransactionRecorder(observability=obs_ctx)
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
        final_request = await orchestrator.process_request(request_message, policy_ctx, obs_ctx)

        # Call backend LLM
        llm_client = LiteLLMClient()

        if is_streaming:
            # Get backend stream and process through pipeline
            backend_stream = await llm_client.stream(final_request)

            # Streaming response in Anthropic format
            return FastAPIStreamingResponse(
                orchestrator.process_streaming_response(backend_stream, policy_ctx, obs_ctx),
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
            return JSONResponse(
                content=anthropic_response,
                headers={"X-Call-ID": call_id},
            )


__all__ = ["router"]
