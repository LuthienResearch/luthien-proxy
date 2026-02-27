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
    +-- process_response
    |   +-- policy_execute
    |   +-- send_upstream (zero or more backend calls)
    +-- send_to_client (non-streaming)
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import TypedDict, TypeGuard, cast

from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic.lib.streaming import MessageStreamEvent
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from opentelemetry import trace
from opentelemetry.context import get_current
from opentelemetry.trace import Span

from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
)
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.pipeline.client_format import ClientFormat
from luthien_proxy.pipeline.session import extract_session_id_from_anthropic_body
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.telemetry import restore_context
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


class _AnthropicPolicyIO(AnthropicPolicyIOProtocol):
    """Request-scoped I/O helpers for execution-oriented Anthropic policies."""

    def __init__(
        self,
        *,
        initial_request: AnthropicRequest,
        anthropic_client: AnthropicClient,
        emitter: EventEmitterProtocol,
        call_id: str,
        session_id: str | None,
    ) -> None:
        self._request = initial_request
        self._initial_request = initial_request
        self._anthropic_client = anthropic_client
        self._emitter = emitter
        self._call_id = call_id
        self._session_id = session_id
        self._backend_call_count = 0
        self._request_recorded = False
        self._first_backend_response: AnthropicResponse | None = None

    @property
    def request(self) -> AnthropicRequest:
        """Current request payload."""
        return self._request

    @property
    def first_backend_response(self) -> AnthropicResponse | None:
        """First backend response observed during this request execution."""
        return self._first_backend_response

    def set_request(self, request: AnthropicRequest) -> None:
        """Replace the current request payload used by backend helper methods."""
        self._request = request

    def ensure_request_recorded(self, final_request: AnthropicRequest | None = None) -> None:
        """Record transaction.request_recorded once for this request lifecycle."""
        if self._request_recorded:
            return

        effective_request = final_request or self._request
        self._emitter.record(
            self._call_id,
            "transaction.request_recorded",
            {
                "original_model": self._initial_request["model"],
                "final_model": effective_request["model"],
                "original_request": dict(self._initial_request),
                "final_request": dict(effective_request),
                "session_id": self._session_id,
            },
        )
        self._request_recorded = True

    def _record_backend_request(self, request: AnthropicRequest) -> None:
        """Record backend request events."""
        self.ensure_request_recorded(request)

        self._emitter.record(
            self._call_id,
            "pipeline.backend_request",
            {"payload": dict(request), "session_id": self._session_id},
        )
        self._backend_call_count += 1

    async def complete(self, request: AnthropicRequest | None = None) -> AnthropicResponse:
        """Execute a non-streaming backend request."""
        final_request = request or self._request
        self._record_backend_request(final_request)

        with tracer.start_as_current_span("send_upstream") as span:
            span.set_attribute("luthien.phase", "send_upstream")
            response = await self._anthropic_client.complete(final_request)

        if self._first_backend_response is None:
            self._first_backend_response = response
        return response

    def stream(self, request: AnthropicRequest | None = None) -> AsyncIterator[MessageStreamEvent]:
        """Execute a streaming backend request."""
        final_request = request or self._request
        self._record_backend_request(final_request)

        async def _stream() -> AsyncIterator[MessageStreamEvent]:
            with tracer.start_as_current_span("send_upstream") as span:
                span.set_attribute("luthien.phase", "send_upstream")
                async for event in self._anthropic_client.stream(final_request):
                    yield event

        return _stream()


def _is_anthropic_response_emission(emitted: AnthropicPolicyEmission) -> TypeGuard[AnthropicResponse]:
    """Detect whether an emission is a non-streaming Anthropic response payload."""
    return isinstance(emitted, dict) and emitted.get("type") == "message" and "role" in emitted and "content" in emitted


async def process_anthropic_request(
    request: Request,
    policy: AnthropicExecutionInterface,
    anthropic_client: AnthropicClient,
    emitter: EventEmitterProtocol,
) -> FastAPIStreamingResponse | JSONResponse:
    """Process an Anthropic API request through the native pipeline.

    Supports execution-oriented Anthropic policies.

    Args:
        request: FastAPI request object
        policy: Anthropic execution policy
        anthropic_client: Client for calling Anthropic API
        emitter: Event emitter for observability

    Returns:
        StreamingResponse or JSONResponse depending on stream parameter

    Raises:
        HTTPException: On request size exceeded or validation errors
        TypeError: If policy does not implement AnthropicExecutionInterface
    """
    if not isinstance(policy, AnthropicExecutionInterface):
        raise TypeError(f"Policy must implement AnthropicExecutionInterface, got {type(policy).__name__}.")

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

        response = await _execute_anthropic_policy(
            execution_policy=policy,
            initial_request=anthropic_request,
            policy_ctx=policy_ctx,
            anthropic_client=anthropic_client,
            emitter=emitter,
            call_id=call_id,
            is_streaming=is_streaming,
            root_span=root_span,
        )

        # Propagate policy summaries if set
        if policy_ctx.request_summary:
            root_span.set_attribute("luthien.policy.request_summary", policy_ctx.request_summary)
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


async def _execute_anthropic_policy(
    execution_policy: AnthropicExecutionInterface,
    initial_request: AnthropicRequest,
    policy_ctx: PolicyContext,
    anthropic_client: AnthropicClient,
    emitter: EventEmitterProtocol,
    call_id: str,
    is_streaming: bool,
    root_span: Span,
) -> FastAPIStreamingResponse | JSONResponse:
    """Execute an Anthropic policy using the execution-oriented runtime."""
    io = _AnthropicPolicyIO(
        initial_request=initial_request,
        anthropic_client=anthropic_client,
        emitter=emitter,
        call_id=call_id,
        session_id=policy_ctx.session_id,
    )
    emissions = execution_policy.run_anthropic(io, policy_ctx)

    if is_streaming:
        return await _handle_execution_streaming(
            emissions=emissions,
            io=io,
            call_id=call_id,
            root_span=root_span,
            policy_ctx=policy_ctx,
        )

    return await _handle_execution_non_streaming(
        emissions=emissions,
        io=io,
        emitter=emitter,
        policy_ctx=policy_ctx,
        call_id=call_id,
    )


async def _handle_execution_streaming(
    emissions: AsyncIterator[AnthropicPolicyEmission],
    io: _AnthropicPolicyIO,
    call_id: str,
    root_span: Span,
    policy_ctx: PolicyContext,
) -> FastAPIStreamingResponse:
    """Handle streaming response flow for execution-oriented policies."""
    parent_context = get_current()

    async def streaming_with_spans() -> AsyncIterator[str]:
        """Wrapper that creates proper span hierarchy for streaming."""
        with restore_context(parent_context):
            chunk_count = 0
            emitted_any = False
            with tracer.start_as_current_span("process_response") as response_span:
                response_span.set_attribute("luthien.phase", "process_response")
                response_span.set_attribute("luthien.streaming", True)

                try:
                    with tracer.start_as_current_span("policy_execute"):
                        async for emitted in emissions:
                            if _is_anthropic_response_emission(emitted):
                                raise TypeError(
                                    "Streaming Anthropic execution policies must emit streaming events, "
                                    "not full response objects."
                                )
                            io.ensure_request_recorded()
                            emitted_any = True
                            chunk_count += 1
                            yield _format_sse_event(cast(MessageStreamEvent, emitted))
                except Exception as e:
                    # Headers may already be sent, so emit an in-stream error event.
                    policy_ctx.record_event(
                        "policy.execution.streaming_error",
                        {"summary": "Execution policy raised during streaming", "error": str(e)},
                    )
                    error_event = _build_error_event(e, call_id)
                    yield _format_sse_event(error_event)
                finally:
                    if not emitted_any:
                        io.ensure_request_recorded()
                        logger.warning(
                            "[%s] Execution policy emitted zero streaming events; returning empty stream",
                            call_id,
                        )
                        policy_ctx.record_event(
                            "policy.execution.empty_stream",
                            {"summary": "Execution policy emitted zero streaming events"},
                        )
                    response_span.set_attribute("streaming.chunk_count", chunk_count)
                    if policy_ctx.response_summary:
                        root_span.set_attribute("luthien.policy.response_summary", policy_ctx.response_summary)

    return FastAPIStreamingResponse(
        streaming_with_spans(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Call-ID": call_id,
        },
    )


async def _handle_execution_non_streaming(
    emissions: AsyncIterator[AnthropicPolicyEmission],
    io: _AnthropicPolicyIO,
    emitter: EventEmitterProtocol,
    policy_ctx: PolicyContext,
    call_id: str,
) -> JSONResponse:
    """Handle non-streaming response flow for execution-oriented policies."""
    final_response: AnthropicResponse | None = None
    response_count = 0

    with tracer.start_as_current_span("process_response") as span:
        span.set_attribute("luthien.phase", "process_response")
        try:
            with tracer.start_as_current_span("policy_execute"):
                async for emitted in emissions:
                    if not _is_anthropic_response_emission(emitted):
                        raise TypeError(
                            "Non-streaming Anthropic execution policies must emit a response object, "
                            "not streaming events."
                        )
                    final_response = emitted
                    response_count += 1
        except Exception as e:
            _handle_anthropic_error(e, call_id)
            raise

    io.ensure_request_recorded()

    if final_response is None:
        raise RuntimeError(
            "Anthropic execution policy did not emit a non-streaming response. "
            "Emit exactly one response object in non-streaming mode."
        )

    if response_count > 1:
        logger.warning("[%s] Execution policy emitted %d non-streaming responses; using last", call_id, response_count)
        policy_ctx.record_event(
            "policy.execution.multiple_non_streaming_responses",
            {"count": response_count, "summary": "Using last emitted response"},
        )

    original_response_payload: dict | None = None
    if io.first_backend_response is not None:
        original_response_payload = dict(io.first_backend_response)

    emitter.record(
        call_id,
        "transaction.non_streaming_response_recorded",
        {
            "original_response": original_response_payload,
            "final_response": dict(final_response),
            "session_id": policy_ctx.session_id,
        },
    )

    with tracer.start_as_current_span("send_to_client") as span:
        span.set_attribute("luthien.phase", "send_to_client")

        emitter.record(
            call_id,
            "pipeline.client_response",
            {"payload": dict(final_response), "session_id": policy_ctx.session_id},
        )

        return JSONResponse(
            content=dict(final_response),
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
        error_type = _ANTHROPIC_STATUS_ERROR_TYPE_MAP.get(e.status_code or 500, "api_error")
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


# Maps Anthropic HTTP status codes to error type strings.
# Aligns with Anthropic's documented error types for proper client formatting.
_ANTHROPIC_STATUS_ERROR_TYPE_MAP: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    409: "conflict_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    529: "overloaded_error",
}


def _handle_anthropic_error(e: Exception, call_id: str) -> None:
    """Handle Anthropic API errors by raising BackendAPIError.

    Uses BackendAPIError instead of HTTPException so the main.py exception
    handler can format the response properly and handle credential
    invalidation on 401.

    Args:
        e: Exception from Anthropic SDK
        call_id: Transaction ID for logging

    Raises:
        BackendAPIError: If the exception is a known Anthropic API error
    """
    if isinstance(e, AnthropicStatusError):
        status_code = e.status_code or 500
        error_type = _ANTHROPIC_STATUS_ERROR_TYPE_MAP.get(status_code, "api_error")
        logger.warning(f"[{call_id}] Anthropic API error: {status_code} {e.message}")
        raise BackendAPIError(
            status_code=status_code,
            message=str(e.message),
            error_type=error_type,
            client_format=ClientFormat.ANTHROPIC,
            provider="anthropic",
        ) from e
    elif isinstance(e, AnthropicConnectionError):
        logger.warning(f"[{call_id}] Anthropic connection error: {e}")
        raise BackendAPIError(
            status_code=502,
            message=str(e),
            error_type="api_connection_error",
            client_format=ClientFormat.ANTHROPIC,
            provider="anthropic",
        ) from e
    # For other exceptions, let them propagate


__all__ = ["process_anthropic_request"]
