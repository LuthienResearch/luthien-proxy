"""OpenAI-format request processing pipeline.

This module provides the entry point for processing OpenAI-format LLM requests.
Anthropic format requests are handled by anthropic_processor.py.

Span Hierarchy
--------------
The pipeline creates a structured span hierarchy for observability:

    transaction_processing (root)
    ├── process_request
    ├── policy_on_request
    │   └── policy.process_request
    ├── send_upstream
    │   └── llm.stream / llm.complete
    ├── process_response
    │   ├── streaming.policy_executor (streaming only)
    │   ├── streaming.client_formatter (streaming only)
    │   └── policy.process_response (non-streaming only)
    └── send_to_client

For streaming, process_response wraps the entire streaming pipeline,
and send_to_client covers the SSE event generation to the client.
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncIterator

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from litellm.types.utils import ModelResponse
from openai import APIConnectionError as OpenAIAPIConnectionError
from openai import APIStatusError as OpenAIAPIStatusError
from opentelemetry import trace
from opentelemetry.context import attach, detach, get_current
from opentelemetry.trace import Span
from pydantic import ValidationError

from luthien_proxy.exceptions import BackendAPIError, map_litellm_error_type
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.llm.types import Request as RequestMessage
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.observability.transaction_recorder import (
    DefaultTransactionRecorder,
)
from luthien_proxy.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.pipeline.client_format import ClientFormat
from luthien_proxy.pipeline.session import extract_session_id_from_headers
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.client_formatter.openai import OpenAIClientFormatter
from luthien_proxy.streaming.policy_executor.executor import PolicyExecutor
from luthien_proxy.types import RawHttpRequest
from luthien_proxy.utils.constants import MAX_REQUEST_MESSAGE_COUNT, MAX_REQUEST_PAYLOAD_BYTES

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def process_llm_request(
    request: Request,
    client_format: ClientFormat,
    policy: OpenAIPolicyInterface,
    llm_client: LLMClient,
    emitter: EventEmitterProtocol,
) -> FastAPIStreamingResponse | JSONResponse:
    """Process an OpenAI-format LLM request through the pipeline.

    The processing pipeline is:
    1. process_request: Ingest and validate request
    2. send_upstream: Send request to backend LLM
    3. process_response: Apply policy to response (streaming or full)
    4. send_to_client: Return response

    Args:
        request: FastAPI request object
        client_format: Format of the client request (kept for compatibility)
        policy: Policy implementing OpenAIPolicyInterface
        llm_client: Client for calling backend LLM
        emitter: Event emitter for observability

    Returns:
        StreamingResponse or JSONResponse depending on stream parameter

    Raises:
        HTTPException: On request size exceeded or other errors
        TypeError: If policy does not implement OpenAIPolicyInterface
    """
    if not isinstance(policy, OpenAIPolicyInterface):
        raise TypeError(
            f"Policy must implement OpenAIPolicyInterface, got {type(policy).__name__}. "
            "Ensure your policy inherits from OpenAIPolicyInterface or implements all required hooks."
        )

    call_id = str(uuid.uuid4())

    # Derive endpoint path from client format for observability
    endpoint = "/v1/messages" if client_format == ClientFormat.ANTHROPIC else "/v1/chat/completions"

    with tracer.start_as_current_span("transaction_processing") as root_span:
        root_span.set_attribute("luthien.transaction_id", call_id)
        root_span.set_attribute("luthien.client_format", client_format.value)
        root_span.set_attribute("luthien.endpoint", endpoint)

        # Phase 1: Process incoming request
        request_message, raw_http_request, session_id = await _process_request(
            request=request,
            client_format=client_format,
            call_id=call_id,
            emitter=emitter,
        )

        is_streaming = request_message.stream
        root_span.set_attribute("luthien.model", request_message.model)
        root_span.set_attribute("luthien.stream", is_streaming)
        if session_id:
            root_span.set_attribute("luthien.session_id", session_id)

        # Create policy context and orchestrator
        policy_ctx = PolicyContext(
            transaction_id=call_id,
            request=request_message,
            emitter=emitter,
            raw_http_request=raw_http_request,
            session_id=session_id,
        )
        recorder = DefaultTransactionRecorder(transaction_id=call_id, emitter=emitter, session_id=session_id)
        policy_executor = PolicyExecutor(recorder=recorder)
        client_formatter = _get_client_formatter(client_format, request_message.model)

        orchestrator = PolicyOrchestrator(
            policy=policy,
            policy_executor=policy_executor,
            client_formatter=client_formatter,
            transaction_recorder=recorder,
        )

        # Set policy name on root span for easy identification
        root_span.set_attribute("luthien.policy.name", policy.__class__.__name__)

        # Apply policy to request
        with tracer.start_as_current_span("policy_on_request"):
            final_request = await orchestrator.process_request(request_message, policy_ctx)

        # Propagate request summary if policy set one
        if policy_ctx.request_summary:
            root_span.set_attribute("luthien.policy.request_summary", policy_ctx.request_summary)

        emitter.record(
            call_id,
            "pipeline.backend_request",
            {"payload": final_request.model_dump(exclude_none=True), "session_id": session_id},
        )

        # Phase 2 & 3 & 4: Send upstream, process response, send to client
        if is_streaming:
            return await _handle_streaming(
                final_request=final_request,
                orchestrator=orchestrator,
                policy_ctx=policy_ctx,
                llm_client=llm_client,
                client_format=client_format,
                call_id=call_id,
                root_span=root_span,
            )
        else:
            response = await _handle_non_streaming(
                final_request=final_request,
                orchestrator=orchestrator,
                policy_ctx=policy_ctx,
                llm_client=llm_client,
                client_format=client_format,
                emitter=emitter,
                call_id=call_id,
            )

            # Propagate response summary if policy set one
            if policy_ctx.response_summary:
                root_span.set_attribute("luthien.policy.response_summary", policy_ctx.response_summary)

            return response


async def _process_request(
    request: Request,
    client_format: ClientFormat,
    call_id: str,
    emitter: EventEmitterProtocol,
) -> tuple[RequestMessage, RawHttpRequest, str | None]:
    """Process and validate incoming OpenAI-format request.

    Args:
        request: FastAPI request object
        client_format: Client API format (kept for compatibility)
        call_id: Transaction ID
        emitter: Event emitter

    Returns:
        Tuple of (RequestMessage, RawHttpRequest with original data, session_id)

    Raises:
        HTTPException: On request size exceeded or invalid format
    """
    with tracer.start_as_current_span("process_request") as span:
        span.set_attribute("luthien.phase", "process_request")

        # Check request size
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_PAYLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Request payload too large")

        body = await request.json()
        headers = {k.lower(): v for k, v in request.headers.items()}

        # Capture raw HTTP request before any processing
        raw_http_request = RawHttpRequest(
            body=body,
            headers=headers,
            method=request.method,
            path=request.url.path,
        )

        # Log incoming request
        emitter.record(call_id, "pipeline.client_request", {"payload": body})

        # Check message count
        messages = body.get("messages", [])
        if isinstance(messages, list) and len(messages) > MAX_REQUEST_MESSAGE_COUNT:
            raise HTTPException(
                status_code=400,
                detail=f"Too many messages: {len(messages)} exceeds limit of {MAX_REQUEST_MESSAGE_COUNT}",
            )

        # Extract session ID from headers
        session_id = extract_session_id_from_headers(headers)
        try:
            request_message = RequestMessage(**body)
        except ValidationError as e:
            logger.error(f"[{call_id}] Failed to parse OpenAI request: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid OpenAI request format: {e}")
        logger.info(f"[{call_id}] /v1/chat/completions: model={request_message.model}, stream={request_message.stream}")

        if session_id:
            span.set_attribute("luthien.session_id", session_id)
            logger.debug(f"[{call_id}] Extracted session_id: {session_id}")

        return request_message, raw_http_request, session_id


def _get_client_formatter(client_format: ClientFormat, model_name: str) -> OpenAIClientFormatter:
    """Get the OpenAI client formatter."""
    return OpenAIClientFormatter(model_name=model_name)


async def _handle_streaming(
    final_request: RequestMessage,
    orchestrator: PolicyOrchestrator,
    policy_ctx: PolicyContext,
    llm_client: LLMClient,
    client_format: ClientFormat,
    call_id: str,
    root_span: Span,
) -> FastAPIStreamingResponse:
    """Handle streaming response flow.

    Phases 2-4 are interleaved for streaming: chunks flow through
    send_upstream → process_response → send_to_client continuously.

    The span hierarchy for streaming is managed by capturing the parent
    context and creating sibling spans within the streaming generator.
    """
    # Capture parent context before entering the generator
    # This allows us to create sibling spans under transaction_processing
    parent_context = get_current()

    with tracer.start_as_current_span("send_upstream") as span:
        span.set_attribute("luthien.phase", "send_upstream")
        try:
            backend_stream = await llm_client.stream(final_request)
        except OpenAIAPIStatusError as e:
            logger.warning(f"[{call_id}] Backend API error: {e.status_code} {e.message}")
            raise BackendAPIError(
                status_code=e.status_code or 500,
                message=str(e.message),
                error_type=map_litellm_error_type(e),
                client_format=client_format,
                provider=getattr(e, "llm_provider", None),
            ) from e
        except OpenAIAPIConnectionError as e:
            logger.warning(f"[{call_id}] Backend connection error: {e.message}")
            raise BackendAPIError(
                status_code=502,
                message=str(e.message),
                error_type="api_connection_error",
                client_format=client_format,
                provider=getattr(e, "llm_provider", None),
            ) from e

    # Create a wrapper generator that manages span context
    async def streaming_with_spans() -> AsyncIterator[str]:
        """Wrapper that creates proper span hierarchy for streaming."""
        # Attach parent context so spans are siblings under transaction_processing
        token = attach(parent_context)
        chunk_count = 0
        try:
            # process_response span wraps the entire streaming pipeline
            with tracer.start_as_current_span("process_response") as response_span:
                response_span.set_attribute("luthien.phase", "process_response")
                response_span.set_attribute("luthien.streaming", True)

                try:
                    # send_to_client is interleaved - we track it as an event
                    async for sse_event in orchestrator.process_streaming_response(backend_stream, policy_ctx):
                        chunk_count += 1
                        yield sse_event
                finally:
                    # Always record chunk count and summary, even on error
                    response_span.set_attribute("streaming.chunk_count", chunk_count)
                    if policy_ctx.response_summary:
                        root_span.set_attribute("luthien.policy.response_summary", policy_ctx.response_summary)
        finally:
            detach(token)

    return FastAPIStreamingResponse(
        streaming_with_spans(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Call-ID": call_id,
        },
    )


async def _handle_non_streaming(
    final_request: RequestMessage,
    orchestrator: PolicyOrchestrator,
    policy_ctx: PolicyContext,
    llm_client: LLMClient,
    client_format: ClientFormat,
    emitter: EventEmitterProtocol,
    call_id: str,
) -> JSONResponse:
    """Handle non-streaming response flow."""
    # Phase 2: Send to upstream
    with tracer.start_as_current_span("send_upstream") as span:
        span.set_attribute("luthien.phase", "send_upstream")
        try:
            response: ModelResponse = await llm_client.complete(final_request)
        except OpenAIAPIStatusError as e:
            logger.warning(f"[{call_id}] Backend API error: {e.status_code} {e.message}")
            raise BackendAPIError(
                status_code=e.status_code or 500,
                message=str(e.message),
                error_type=map_litellm_error_type(e),
                client_format=client_format,
                provider=getattr(e, "llm_provider", None),
            ) from e
        except OpenAIAPIConnectionError as e:
            logger.warning(f"[{call_id}] Backend connection error: {e.message}")
            raise BackendAPIError(
                status_code=502,
                message=str(e.message),
                error_type="api_connection_error",
                client_format=client_format,
                provider=getattr(e, "llm_provider", None),
            ) from e

    # Phase 3: Process response through policy
    with tracer.start_as_current_span("process_response") as span:
        span.set_attribute("luthien.phase", "process_response")
        processed_response = await orchestrator.process_full_response(response, policy_ctx)

    # Phase 4: Send to client
    with tracer.start_as_current_span("send_to_client") as span:
        span.set_attribute("luthien.phase", "send_to_client")

        final_response = processed_response.model_dump()

        emitter.record(
            call_id, "pipeline.client_response", {"payload": final_response, "session_id": policy_ctx.session_id}
        )

        return JSONResponse(
            content=final_response,
            headers={"X-Call-ID": call_id},
        )


__all__ = ["process_llm_request"]
