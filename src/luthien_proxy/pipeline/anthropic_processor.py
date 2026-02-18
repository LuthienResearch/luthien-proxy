# ABOUTME: Anthropic-native request processing pipeline
# ABOUTME: Processes Anthropic requests through AnthropicPolicyInterface without format conversion

"""Anthropic-native request processing pipeline.

This module provides a dedicated processing pipeline for Anthropic API requests,
using the native Anthropic types throughout without converting to OpenAI format.
This preserves Anthropic-specific features like extended thinking, tool use patterns,
and prompt caching.

Span Hierarchy
--------------
The pipeline creates a structured span hierarchy for observability:

    anthropic_transaction_processing (root)
    +-- process_request
    +-- policy_on_request
    +-- send_upstream
    |   +-- anthropic.stream / anthropic.complete
    +-- process_response
    |   +-- anthropic.stream_executor (streaming only)
    +-- send_to_client
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator, TypedDict

from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic import BadRequestError as AnthropicBadRequestError
from anthropic.lib.streaming import MessageStreamEvent
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from opentelemetry import trace
from opentelemetry.context import attach, detach, get_current
from opentelemetry.trace import Span

from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
)
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.pipeline.session import extract_session_id_from_anthropic_body
from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.anthropic_executor import AnthropicStreamExecutor
from luthien_proxy.types import RawHttpRequest
from luthien_proxy.utils.constants import MAX_REQUEST_PAYLOAD_BYTES


class _ErrorDetail(TypedDict):
    """Error detail structure for mid-stream error events."""

    type: str
    message: str


class _StreamErrorEvent(TypedDict):
    """Error event for mid-stream failures (when HTTP headers already sent)."""

    type: str
    error: _ErrorDetail


logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def process_anthropic_request(
    request: Request,
    policy: AnthropicPolicyInterface,
    anthropic_client: AnthropicClient,
    emitter: EventEmitterProtocol,
) -> FastAPIStreamingResponse | JSONResponse:
    """Process an Anthropic API request through the native pipeline.

    This function handles Anthropic requests without converting to OpenAI format.
    It uses AnthropicPolicyInterface for policy hooks and AnthropicClient for
    backend calls.

    The processing pipeline is:
    1. process_request: Parse and validate incoming request
    2. policy_on_request: Apply policy to request
    3. send_upstream: Send request to Anthropic API
    4. process_response: Apply policy to response (streaming or full)
    5. send_to_client: Return response

    Args:
        request: FastAPI request object
        policy: Policy implementing AnthropicPolicyInterface
        anthropic_client: Client for calling Anthropic API
        emitter: Event emitter for observability

    Returns:
        StreamingResponse or JSONResponse depending on stream parameter

    Raises:
        HTTPException: On request size exceeded or validation errors
        TypeError: If policy does not implement AnthropicPolicyInterface
    """
    if not isinstance(policy, AnthropicPolicyInterface):
        raise TypeError(
            f"Policy must implement AnthropicPolicyInterface, got {type(policy).__name__}. "
            "Ensure your policy inherits from AnthropicPolicyInterface or implements all required hooks."
        )

    call_id = str(uuid.uuid4())

    with tracer.start_as_current_span("anthropic_transaction_processing") as root_span:
        root_span.set_attribute("luthien.transaction_id", call_id)
        root_span.set_attribute("luthien.client_format", "anthropic_native")
        root_span.set_attribute("luthien.endpoint", "/v1/messages")

        # Phase 1: Process incoming request
        anthropic_request, raw_http_request, session_id = await _process_request(
            request=request,
            call_id=call_id,
            emitter=emitter,
        )

        is_streaming = anthropic_request.get("stream", False)
        model = anthropic_request["model"]
        root_span.set_attribute("luthien.model", model)
        root_span.set_attribute("luthien.stream", is_streaming)
        if session_id:
            root_span.set_attribute("luthien.session_id", session_id)

        # Create policy context
        policy_ctx = PolicyContext(
            transaction_id=call_id,
            request=None,  # No OpenAI-format request for native Anthropic path
            emitter=emitter,
            raw_http_request=raw_http_request,
            session_id=session_id,
        )

        # Set policy name on root span for easy identification
        root_span.set_attribute("luthien.policy.name", policy.__class__.__name__)

        # Apply policy to request
        with tracer.start_as_current_span("policy_on_request"):
            final_request = await policy.on_anthropic_request(anthropic_request, policy_ctx)

        # Propagate request summary if policy set one
        if policy_ctx.request_summary:
            root_span.set_attribute("luthien.policy.request_summary", policy_ctx.request_summary)

        emitter.record(
            call_id,
            "pipeline.backend_request",
            {"payload": dict(final_request), "session_id": session_id},
        )

        # Phase 2-4: Send upstream, process response, send to client
        if is_streaming:
            return await _handle_streaming(
                final_request=final_request,
                original_request=anthropic_request,
                policy=policy,
                policy_ctx=policy_ctx,
                anthropic_client=anthropic_client,
                emitter=emitter,
                call_id=call_id,
                root_span=root_span,
            )
        else:
            response = await _handle_non_streaming(
                final_request=final_request,
                original_request=anthropic_request,
                policy=policy,
                policy_ctx=policy_ctx,
                anthropic_client=anthropic_client,
                emitter=emitter,
                call_id=call_id,
            )

            # Propagate response summary if policy set one
            if policy_ctx.response_summary:
                root_span.set_attribute("luthien.policy.response_summary", policy_ctx.response_summary)

            return response


async def _process_request(
    request: Request,
    call_id: str,
    emitter: EventEmitterProtocol,
) -> tuple[AnthropicRequest, RawHttpRequest, str | None]:
    """Process and validate incoming Anthropic request.

    Args:
        request: FastAPI request object
        call_id: Transaction ID
        emitter: Event emitter

    Returns:
        Tuple of (AnthropicRequest, RawHttpRequest with original data, session_id)

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

        # Extract session ID from Anthropic metadata
        session_id = extract_session_id_from_anthropic_body(body)

        # Validate required fields
        if "model" not in body:
            raise HTTPException(status_code=400, detail="Missing required field: model")
        if "messages" not in body:
            raise HTTPException(status_code=400, detail="Missing required field: messages")
        if "max_tokens" not in body:
            raise HTTPException(status_code=400, detail="Missing required field: max_tokens")

        # Create typed request
        anthropic_request: AnthropicRequest = body

        if session_id:
            span.set_attribute("luthien.session_id", session_id)
            logger.debug(f"[{call_id}] Extracted session_id: {session_id}")

        logger.info(
            f"[{call_id}] /v1/messages (native): model={anthropic_request['model']}, "
            f"stream={anthropic_request.get('stream', False)}"
        )

        return anthropic_request, raw_http_request, session_id


def _should_attempt_passthrough(
    original_request: AnthropicRequest,
    final_request: AnthropicRequest,
) -> bool:
    """Check if passthrough fallback should be attempted.

    Passthrough is only worthwhile when the policy actually modified
    the request — if original == final, the same request would fail again.
    """
    return dict(original_request) != dict(final_request)


async def _handle_streaming(
    final_request: AnthropicRequest,
    original_request: AnthropicRequest,
    policy: AnthropicPolicyInterface,
    policy_ctx: PolicyContext,
    anthropic_client: AnthropicClient,
    emitter: EventEmitterProtocol,
    call_id: str,
    root_span: Span,
) -> FastAPIStreamingResponse:
    """Handle streaming response flow.

    Phases 2-4 are interleaved for streaming: chunks flow through
    send_upstream -> process_response -> send_to_client continuously.
    """
    # Capture parent context before entering the generator
    parent_context = get_current()

    def _on_auto_fix(fix_type: str, data: dict[str, Any]) -> None:
        emitter.record(call_id, "pipeline.auto_fix", {"fix_type": fix_type, **data})

    with tracer.start_as_current_span("send_upstream") as span:
        span.set_attribute("luthien.phase", "send_upstream")
        # Note: stream() returns an async iterator, not an awaitable
        # The actual API call happens when we start iterating
        backend_stream = anthropic_client.stream(final_request, on_auto_fix=_on_auto_fix)

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
                    executor = AnthropicStreamExecutor()
                    async for event in executor.process(backend_stream, policy, policy_ctx):
                        chunk_count += 1
                        sse_line = _format_sse_event(event)
                        yield sse_line
                except AnthropicBadRequestError as e:
                    if chunk_count == 0 and _should_attempt_passthrough(original_request, final_request):
                        logger.info("[%s] Pipeline 400 before stream started — trying passthrough", call_id)
                        emitter.record(call_id, "pipeline.passthrough_fallback", {"error": str(e.message)})
                        response_span.set_attribute("luthien.passthrough_fallback", True)
                        passthrough_stream = anthropic_client.stream_passthrough(dict(original_request))
                        async for event in passthrough_stream:
                            chunk_count += 1
                            yield _format_sse_event(event)
                    else:
                        error_event = _build_error_event(e, call_id)
                        yield _format_sse_event(error_event)
                except (AnthropicStatusError, AnthropicConnectionError) as e:
                    error_event = _build_error_event(e, call_id)
                    yield _format_sse_event(error_event)
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
    final_request: AnthropicRequest,
    original_request: AnthropicRequest,
    policy: AnthropicPolicyInterface,
    policy_ctx: PolicyContext,
    anthropic_client: AnthropicClient,
    emitter: EventEmitterProtocol,
    call_id: str,
) -> JSONResponse:
    """Handle non-streaming response flow."""

    def _on_auto_fix(fix_type: str, data: dict[str, Any]) -> None:
        emitter.record(call_id, "pipeline.auto_fix", {"fix_type": fix_type, **data})

    # Phase 2: Send to upstream
    with tracer.start_as_current_span("send_upstream") as span:
        span.set_attribute("luthien.phase", "send_upstream")
        try:
            response: AnthropicResponse = await anthropic_client.complete(final_request, on_auto_fix=_on_auto_fix)
        except AnthropicBadRequestError as e:
            if _should_attempt_passthrough(original_request, final_request):
                logger.info("[%s] Pipeline 400 — trying passthrough", call_id)
                emitter.record(call_id, "pipeline.passthrough_fallback", {"error": str(e.message)})
                span.set_attribute("luthien.passthrough_fallback", True)
                try:
                    response = await anthropic_client.complete_passthrough(dict(original_request))
                except Exception as passthrough_e:
                    _handle_anthropic_error(passthrough_e, call_id)
                    raise
            else:
                _handle_anthropic_error(e, call_id)
                raise
        except Exception as e:
            _handle_anthropic_error(e, call_id)
            raise  # Re-raise if not handled

    # Phase 3: Process response through policy
    with tracer.start_as_current_span("process_response") as span:
        span.set_attribute("luthien.phase", "process_response")
        processed_response = await policy.on_anthropic_response(response, policy_ctx)

    # Phase 4: Send to client
    with tracer.start_as_current_span("send_to_client") as span:
        span.set_attribute("luthien.phase", "send_to_client")

        emitter.record(
            call_id,
            "pipeline.client_response",
            {"payload": dict(processed_response), "session_id": policy_ctx.session_id},
        )

        return JSONResponse(
            content=dict(processed_response),
            headers={"X-Call-ID": call_id},
        )


def _format_sse_event(event: MessageStreamEvent | _StreamErrorEvent) -> str:
    """Format an Anthropic stream event as an SSE line.

    Args:
        event: Anthropic SDK streaming event (Pydantic model) or error event dict

    Returns:
        SSE-formatted string with event type and JSON data.
    """
    # Handle both SDK Pydantic models and TypedDicts (error events)
    if isinstance(event, dict):
        event_type = str(event.get("type", "unknown"))
        event_data: dict = dict(event)
    else:
        event_type = event.type
        event_data = event.model_dump()

    json_data = json.dumps(event_data)
    return f"event: {event_type}\ndata: {json_data}\n\n"


def _build_error_event(e: Exception, call_id: str) -> _StreamErrorEvent:
    """Build an Anthropic-format error event for mid-stream errors.

    When errors occur after headers are sent, we can't return an HTTP error.
    Instead, emit an error event in the stream so clients can detect the failure.

    Args:
        e: Exception that occurred
        call_id: Transaction ID for logging

    Returns:
        Error event dict with error details
    """
    if isinstance(e, AnthropicStatusError):
        error_type = "api_error"
        message = str(e.message)
        logger.warning(f"[{call_id}] Mid-stream Anthropic API error: {e.status_code} {message}")
    elif isinstance(e, AnthropicConnectionError):
        error_type = "api_connection_error"
        message = str(e)
        logger.warning(f"[{call_id}] Mid-stream Anthropic connection error: {message}")
    else:
        error_type = "api_error"
        message = str(e)
        logger.warning(f"[{call_id}] Mid-stream error: {message}")

    return _StreamErrorEvent(
        type="error",
        error=_ErrorDetail(
            type=error_type,
            message=message,
        ),
    )


def _handle_anthropic_error(e: Exception, call_id: str) -> None:
    """Handle Anthropic API errors by logging and raising HTTPException.

    Args:
        e: Exception from Anthropic SDK
        call_id: Transaction ID for logging

    Raises:
        HTTPException: If the exception is a known Anthropic API error
    """
    if isinstance(e, AnthropicStatusError):
        status_code = e.status_code or 500
        logger.warning(f"[{call_id}] Anthropic API error: {status_code} {e.message}")
        # TODO: invalidate cached credential on 401 (on_backend_401).
        # The OpenAI/LiteLLM path handles this via BackendAPIError in main.py,
        # but this path raises HTTPException which bypasses that handler.
        raise HTTPException(
            status_code=status_code,
            detail={"type": "error", "error": {"type": "api_error", "message": str(e.message)}},
        ) from e
    elif isinstance(e, AnthropicConnectionError):
        logger.warning(f"[{call_id}] Anthropic connection error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"type": "error", "error": {"type": "api_connection_error", "message": str(e)}},
        ) from e
    # For other exceptions, let them propagate


__all__ = ["process_anthropic_request"]
