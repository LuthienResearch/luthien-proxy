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

import copy
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Literal, TypedDict, TypeGuard, cast

from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic.lib.streaming import MessageStreamEvent
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from opentelemetry import trace
from opentelemetry.context import get_current
from opentelemetry.trace import Span

from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.credentials import Credential, CredentialError
from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.llm.anthropic_client import AnthropicClient, serialize_no_extras
from luthien_proxy.llm.types.anthropic import (
    AnthropicContentBlock,
    AnthropicRequest,
    AnthropicResponse,
    build_usage,
)
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.pipeline.client_format import ClientFormat
from luthien_proxy.pipeline.policy_context_injection import inject_policy_awareness_anthropic
from luthien_proxy.pipeline.session import (
    extract_session_id_from_anthropic_body,
    extract_session_id_from_headers,
)
from luthien_proxy.pipeline.stream_protocol_validator import validate_anthropic_event_ordering
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
)
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.request_log.recorder import RequestLogRecorder, create_recorder
from luthien_proxy.settings import client_error_detail, get_settings
from luthien_proxy.telemetry import restore_context
from luthien_proxy.types import RawHttpRequest
from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.utils import db
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
        request_log_recorder: RequestLogRecorder,
        is_streaming: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._request = initial_request
        self._initial_request = initial_request
        self._anthropic_client = anthropic_client
        self._emitter = emitter
        self._call_id = call_id
        self._session_id = session_id
        self._request_log_recorder = request_log_recorder
        self._is_streaming = is_streaming
        self._extra_headers = extra_headers
        self._request_recorded = False
        self._first_backend_response: AnthropicResponse | None = None
        # Raw backend events are only buffered when needed for non-streaming
        # response reconstruction (e.g., diff recording). Streaming responses
        # can reconstruct from the post-policy accumulated_events instead,
        # avoiding duplicate event buffering that doubles memory usage.
        self._buffer_raw_events = not is_streaming
        self._raw_backend_events: list[MessageStreamEvent] = []

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

        request_payload = dict(request)
        self._emitter.record(
            self._call_id,
            "pipeline.backend_request",
            {"payload": request_payload, "session_id": self._session_id},
        )
        self._request_log_recorder.record_outbound_request(
            body=request_payload,
            model=request["model"],
            is_streaming=self._is_streaming,
            endpoint="/v1/messages",
        )

    async def complete(self, request: AnthropicRequest | None = None) -> AnthropicResponse:
        """Execute a non-streaming backend request."""
        final_request = request or self._request
        self._record_backend_request(final_request)

        with tracer.start_as_current_span("send_upstream") as span:
            span.set_attribute("luthien.phase", "send_upstream")
            response = await self._anthropic_client.complete(final_request, extra_headers=self._extra_headers)

        if self._first_backend_response is None:
            # Deep-copy to preserve pre-policy content (policies may mutate in-place)
            self._first_backend_response = copy.deepcopy(response)
        return response

    def stream(self, request: AnthropicRequest | None = None) -> AsyncIterator[MessageStreamEvent]:
        """Execute a streaming backend request."""
        final_request = request or self._request
        self._record_backend_request(final_request)

        extra_headers = self._extra_headers

        async def _stream() -> AsyncIterator[MessageStreamEvent]:
            with tracer.start_as_current_span("send_upstream") as span:
                span.set_attribute("luthien.phase", "send_upstream")
                async for event in self._anthropic_client.stream(final_request, extra_headers=extra_headers):
                    if self._buffer_raw_events:
                        self._raw_backend_events.append(event)
                    yield event

        return _stream()


def _reconstruct_response_from_stream_events(
    events: list[MessageStreamEvent],
) -> AnthropicResponse | None:
    """Reconstruct a complete AnthropicResponse from Anthropic SDK streaming events.

    Accumulates message_start, content_block_*, and message_delta events to rebuild
    the full response for storage in conversation history.

    Returns None if the stream lacked sufficient events to reconstruct (e.g., errored
    before message_start).
    """
    message_id: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    stop_reason: str | None = None
    stop_sequence: str | None = None

    # Index-keyed content block accumulation (handles multiple blocks / tool use)
    blocks_by_index: dict[int, dict] = {}
    json_bufs: dict[int, str] = {}  # partial JSON accumulator for tool_use inputs

    for event in events:
        t = event.type  # type: ignore[union-attr]

        if t == "message_start":
            msg = event.message  # type: ignore[union-attr]
            message_id = msg.id
            model = msg.model
            if msg.usage:
                input_tokens = msg.usage.input_tokens
                cache_creation_input_tokens = msg.usage.cache_creation_input_tokens
                cache_read_input_tokens = msg.usage.cache_read_input_tokens

        elif t == "content_block_start":
            idx: int = event.index  # type: ignore[union-attr]
            cb = event.content_block  # type: ignore[union-attr]
            if cb.type == "text":
                blocks_by_index[idx] = {"type": "text", "text": ""}
            elif cb.type == "tool_use":
                blocks_by_index[idx] = {"type": "tool_use", "id": cb.id, "name": cb.name, "input": {}}
                json_bufs[idx] = ""
            # thinking blocks are intentionally excluded from history

        elif t == "content_block_delta":
            idx = event.index  # type: ignore[union-attr]
            delta = event.delta  # type: ignore[union-attr]
            if idx in blocks_by_index:
                block = blocks_by_index[idx]
                if delta.type == "text_delta" and block["type"] == "text":  # type: ignore[union-attr]
                    block["text"] += delta.text  # type: ignore[union-attr]
                elif delta.type == "input_json_delta" and block["type"] == "tool_use":  # type: ignore[union-attr]
                    json_bufs[idx] = json_bufs.get(idx, "") + delta.partial_json  # type: ignore[union-attr]

        elif t == "content_block_stop":
            idx = event.index  # type: ignore[union-attr]
            if idx in json_bufs and idx in blocks_by_index:
                try:
                    blocks_by_index[idx]["input"] = json.loads(json_bufs[idx])
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Failed to parse tool input JSON for block {idx}: {repr(e)}")
                del json_bufs[idx]

        elif t == "message_delta":
            delta = event.delta  # type: ignore[union-attr]
            stop_reason = getattr(delta, "stop_reason", None)
            stop_sequence = getattr(delta, "stop_sequence", None)
            usage = getattr(event, "usage", None)
            if usage:
                output_tokens = getattr(usage, "output_tokens", 0)
                _cache_create = getattr(usage, "cache_creation_input_tokens", None)
                _cache_read = getattr(usage, "cache_read_input_tokens", None)
                if _cache_create is not None:
                    cache_creation_input_tokens = _cache_create
                if _cache_read is not None:
                    cache_read_input_tokens = _cache_read

    if message_id is None or model is None:
        return None

    content = cast(
        "list[AnthropicContentBlock]",
        [blocks_by_index[i] for i in sorted(blocks_by_index.keys())],
    )
    return AnthropicResponse(
        id=message_id,
        type="message",
        role="assistant",
        content=content,
        model=model,
        stop_reason=cast(
            "Literal['end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'] | None",
            stop_reason,
        ),
        stop_sequence=stop_sequence,
        usage=build_usage(input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens),
    )


def _is_anthropic_response_emission(emitted: AnthropicPolicyEmission) -> TypeGuard[AnthropicResponse]:
    """Detect whether an emission is a non-streaming Anthropic response payload."""
    return (
        isinstance(emitted, dict)
        and emitted.get("type") == "message"
        and isinstance(emitted.get("id"), str)
        and emitted["id"].startswith("msg_")
        and "role" in emitted
        and "content" in emitted
    )


async def process_anthropic_request(
    request: Request,
    policy: AnthropicExecutionInterface,
    anthropic_client: AnthropicClient,
    emitter: EventEmitterProtocol,
    db_pool: db.DatabasePool | None = None,
    enable_request_logging: bool = False,
    usage_collector: UsageCollector | None = None,
    user_credential: Credential | None = None,
    credential_manager: CredentialManager | None = None,
) -> FastAPIStreamingResponse | JSONResponse:
    """Process an Anthropic API request through the native pipeline.

    Supports execution-oriented Anthropic policies.

    Args:
        request: FastAPI request object
        policy: Anthropic execution policy
        anthropic_client: Client for calling Anthropic API
        emitter: Event emitter for observability
        db_pool: Database connection pool for request logging
        enable_request_logging: Whether to record HTTP-level request/response logs
        usage_collector: Optional usage telemetry collector for counting requests
        user_credential: The credential extracted from the incoming request
        credential_manager: Shared credential manager for auth provider resolution

    Returns:
        StreamingResponse or JSONResponse depending on stream parameter

    Raises:
        HTTPException: On request size exceeded or validation errors
        TypeError: If policy does not implement AnthropicExecutionInterface
    """
    if not isinstance(policy, AnthropicExecutionInterface):
        raise TypeError(f"Policy must implement AnthropicExecutionInterface, got {type(policy).__name__}.")

    call_id = str(uuid.uuid4())
    request_log_recorder = create_recorder(db_pool, call_id, enable_request_logging)

    if usage_collector:
        usage_collector.record_accepted()

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

        if get_settings().inject_policy_context and isinstance(policy, BasePolicy):
            anthropic_request = inject_policy_awareness_anthropic(anthropic_request, policy.active_policy_names())

        is_streaming = anthropic_request.get("stream", False)
        model = anthropic_request["model"]
        root_span.set_attribute("luthien.model", model)
        root_span.set_attribute("luthien.stream", is_streaming)
        if session_id:
            root_span.set_attribute("luthien.session_id", session_id)
        if usage_collector:
            usage_collector.record_session(session_id)

        request_log_recorder.record_inbound_request(
            method=raw_http_request.method,
            url=raw_http_request.path,
            headers=raw_http_request.headers,
            body=dict(raw_http_request.body),
            session_id=session_id,
            model=model,
            is_streaming=is_streaming,
            endpoint="/v1/messages",
        )

        # Forward anthropic-beta header from client so beta features (e.g. prompt
        # caching with scope) aren't rejected by the upstream API.
        forwarded_headers: dict[str, str] | None = None
        if beta := raw_http_request.headers.get("anthropic-beta"):
            forwarded_headers = {"anthropic-beta": beta}

        # Create policy context
        policy_ctx = PolicyContext(
            transaction_id=call_id,
            request=None,  # No OpenAI-format request for native Anthropic path
            emitter=emitter,
            raw_http_request=raw_http_request,
            session_id=session_id,
            user_credential=user_credential,
            credential_manager=credential_manager,
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
            request_log_recorder=request_log_recorder,
            extra_headers=forwarded_headers,
            usage_collector=usage_collector,
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

        try:
            body = await request.json()
        except json.JSONDecodeError as e:
            logger.error(f"[{call_id}] Malformed JSON in Anthropic request: {repr(e)}")
            raise HTTPException(status_code=400, detail="Invalid JSON in request body")
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

        # Extract session ID: try metadata.user_id first (API key mode),
        # fall back to x-session-id header (OAuth passthrough mode)
        session_id = extract_session_id_from_anthropic_body(body) or extract_session_id_from_headers(headers)

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


async def _run_policy_hooks(
    policy: AnthropicExecutionInterface,
    io: AnthropicPolicyIOProtocol,
    ctx: PolicyContext,
) -> AsyncGenerator[AnthropicPolicyEmission, None]:
    """Call policy hooks around backend I/O.

    The executor owns the stream-vs-complete branching and calls hooks at each
    lifecycle point. This replaces the per-policy execution loops that were
    previously duplicated across multiple policy classes.
    """
    request = await policy.on_anthropic_request(io.request, ctx)
    io.set_request(request)

    if request.get("stream", False):
        async for event in io.stream(request):
            for emitted in await policy.on_anthropic_stream_event(event, ctx):
                yield emitted
        for emitted in await policy.on_anthropic_stream_complete(ctx):
            yield emitted
        return

    response = await io.complete(request)
    yield await policy.on_anthropic_response(response, ctx)


async def _execute_anthropic_policy(
    execution_policy: AnthropicExecutionInterface,
    initial_request: AnthropicRequest,
    policy_ctx: PolicyContext,
    anthropic_client: AnthropicClient,
    emitter: EventEmitterProtocol,
    call_id: str,
    is_streaming: bool,
    root_span: Span,
    request_log_recorder: RequestLogRecorder,
    extra_headers: dict[str, str] | None = None,
    usage_collector: UsageCollector | None = None,
) -> FastAPIStreamingResponse | JSONResponse:
    """Execute an Anthropic policy using the hook-based runtime."""
    io = _AnthropicPolicyIO(
        initial_request=initial_request,
        anthropic_client=anthropic_client,
        emitter=emitter,
        call_id=call_id,
        session_id=policy_ctx.session_id,
        request_log_recorder=request_log_recorder,
        is_streaming=is_streaming,
        extra_headers=extra_headers,
    )
    emissions = _run_policy_hooks(execution_policy, io, policy_ctx)

    if is_streaming:
        return await _handle_execution_streaming(
            emissions=emissions,
            io=io,
            call_id=call_id,
            root_span=root_span,
            policy_ctx=policy_ctx,
            request_log_recorder=request_log_recorder,
            emitter=emitter,
            usage_collector=usage_collector,
        )

    return await _handle_execution_non_streaming(
        emissions=emissions,
        io=io,
        emitter=emitter,
        policy_ctx=policy_ctx,
        call_id=call_id,
        request_log_recorder=request_log_recorder,
        usage_collector=usage_collector,
    )


async def _handle_execution_streaming(
    emissions: AsyncIterator[AnthropicPolicyEmission],
    io: _AnthropicPolicyIO,
    call_id: str,
    root_span: Span,
    policy_ctx: PolicyContext,
    request_log_recorder: RequestLogRecorder,
    emitter: EventEmitterProtocol,
    usage_collector: UsageCollector | None = None,
) -> FastAPIStreamingResponse:
    """Handle streaming response flow for execution-oriented policies."""
    parent_context = get_current()

    async def streaming_with_spans() -> AsyncIterator[str]:
        """Wrapper that creates proper span hierarchy for streaming."""
        with restore_context(parent_context):
            chunk_count = 0
            emitted_any = False
            final_status = 200
            accumulated_events: list[MessageStreamEvent] = []
            with tracer.start_as_current_span("process_response") as response_span:
                response_span.set_attribute("luthien.phase", "process_response")
                response_span.set_attribute("luthien.streaming", True)

                caught_exception = False
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
                            accumulated_events.append(cast(MessageStreamEvent, emitted))
                            sse = _format_sse_event(cast(MessageStreamEvent, emitted))
                            if sse is not None:
                                yield sse
                except Exception as e:
                    caught_exception = True
                    # Headers may already be sent, so emit an in-stream error event.
                    policy_ctx.record_event(
                        "policy.execution.streaming_error",
                        {"summary": "Execution policy raised during streaming", "error": repr(e)},
                    )
                    if isinstance(e, AnthropicStatusError):
                        final_status = e.status_code or 500
                    elif isinstance(e, AnthropicConnectionError):
                        final_status = 503
                    else:
                        final_status = 500
                    error_event = _build_error_event(e, call_id)
                    yield cast(str, _format_sse_event(error_event))
                finally:
                    if not emitted_any and not caught_exception:
                        io.ensure_request_recorded()
                        logger.warning(
                            "[%s] Execution policy emitted zero streaming events; yielding error event",
                            call_id,
                        )
                        policy_ctx.record_event(
                            "policy.execution.empty_stream",
                            {"summary": "Execution policy emitted zero streaming events"},
                        )
                        final_status = 500
                        # Yield an Anthropic-compatible error event so the client
                        # gets a clear signal instead of a silent empty HTTP 200.
                        empty_stream_error = _StreamErrorEvent(
                            type="error",
                            error=_ErrorDetail(
                                type="api_error",
                                message="Request blocked: policy evaluation unavailable. Contact your administrator.",
                            ),
                        )
                        yield cast(str, _format_sse_event(empty_stream_error))
                    response_span.set_attribute("streaming.chunk_count", chunk_count)

                    # Validate streaming protocol compliance (log-and-warn).
                    # Only validate complete streams — partial/error streams will
                    # always fail completeness checks (missing message_stop, etc.)
                    # and produce noisy false positives.
                    if accumulated_events and final_status == 200:
                        validation = validate_anthropic_event_ordering(accumulated_events)
                        if not validation.valid:
                            violation_details = [
                                {"rule": v.rule, "message": v.message, "event_index": v.event_index}
                                for v in validation.violations
                            ]
                            logger.warning(
                                "[%s] Streaming protocol violation detected: %s",
                                call_id,
                                violation_details,
                            )
                            policy_ctx.record_event(
                                "streaming.protocol_violation",
                                {
                                    "summary": "Outbound stream violates Anthropic event ordering",
                                    "violations": violation_details,
                                },
                            )
                            response_span.set_attribute("streaming.protocol_valid", False)
                        else:
                            response_span.set_attribute("streaming.protocol_valid", True)

                    if policy_ctx.response_summary:
                        root_span.set_attribute("luthien.policy.response_summary", policy_ctx.response_summary)
                    reconstructed = _reconstruct_response_from_stream_events(accumulated_events)
                    if reconstructed is not None:
                        # Use raw backend events for original response if buffered,
                        # otherwise fall back to accumulated (post-policy) events.
                        # Trade-off: for streaming requests, raw events are NOT buffered
                        # separately (_buffer_raw_events=False) to avoid doubling memory
                        # usage. This means the diff viewer will show identical original
                        # and final responses for streaming requests. Non-streaming
                        # requests still capture true pre-policy vs post-policy diffs.
                        raw_events = accumulated_events if not io._buffer_raw_events else io._raw_backend_events
                        raw_reconstructed = _reconstruct_response_from_stream_events(raw_events)
                        emitter.record(
                            call_id,
                            "transaction.streaming_response_recorded",
                            {
                                "original_response": dict(raw_reconstructed)
                                if raw_reconstructed is not None
                                else dict(reconstructed),
                                "final_response": dict(reconstructed),
                                "session_id": policy_ctx.session_id,
                            },
                        )
                    request_log_recorder.record_inbound_response(status=final_status)
                    request_log_recorder.record_outbound_response(status=final_status)
                    request_log_recorder.flush()
                    if usage_collector and final_status == 200:
                        usage_collector.record_completed(is_streaming=True)
                        if reconstructed is not None and "usage" in reconstructed:
                            usage = reconstructed["usage"]
                            usage_collector.record_tokens(
                                input_tokens=usage.get("input_tokens", 0),
                                output_tokens=usage.get("output_tokens", 0),
                            )

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
    request_log_recorder: RequestLogRecorder,
    usage_collector: UsageCollector | None = None,
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
            # _handle_anthropic_error only raises for Anthropic SDK errors; for
            # anything else (policy logic errors, etc.) convert to a safe 500
            # so internal details are not exposed to the client.
            logger.error("[%s] Unexpected error in non-streaming policy execution: %s", call_id, e)
            raise BackendAPIError(
                status_code=500,
                message=client_error_detail(str(e), "An internal error occurred while processing the request."),
                error_type="api_error",
                client_format=ClientFormat.ANTHROPIC,
            ) from e

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
        final_response_payload = dict(final_response)

        emitter.record(
            call_id,
            "pipeline.client_response",
            {"payload": final_response_payload, "session_id": policy_ctx.session_id},
        )
        request_log_recorder.record_outbound_response(body=final_response_payload, status=200)
        request_log_recorder.record_inbound_response(status=200, body=final_response_payload)
        request_log_recorder.flush()

        if usage_collector:
            usage_collector.record_completed(is_streaming=False)
            usage = final_response.get("usage")
            if usage:
                usage_collector.record_tokens(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )

        return JSONResponse(
            content=final_response_payload,
            headers={"X-Call-ID": call_id},
        )


_SYNTHETIC_EVENT_TYPES = frozenset({"text", "thinking", "citation", "signature", "input_json"})

_STOP_EVENT_WIRE_FIELDS: dict[str, frozenset[str]] = {
    "content_block_stop": frozenset({"type", "index"}),
    "message_stop": frozenset({"type"}),
}


def _format_sse_event(event: MessageStreamEvent | _StreamErrorEvent) -> str | None:
    if isinstance(event, dict):
        event_type = str(event.get("type", "unknown"))
        event_data: dict = dict(event)
    else:
        event_type = event.type
        if event_type in _SYNTHETIC_EVENT_TYPES:
            return None
        serialized = serialize_no_extras(event)
        wire_fields = _STOP_EVENT_WIRE_FIELDS.get(event_type)
        event_data = {k: v for k, v in serialized.items() if k in wire_fields} if wire_fields else serialized

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
        message = client_error_detail(str(e), "An error occurred while connecting to the API.")
        logger.warning(f"[{call_id}] Mid-stream Anthropic connection error: {repr(e)}")
    else:
        error_type = "api_error"
        message = client_error_detail(str(e), "An internal error occurred while processing the request.")
        logger.error(f"[{call_id}] Mid-stream error: {repr(e)}")

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
    if isinstance(e, CredentialError):
        logger.warning(f"[{call_id}] Credential error during policy execution: {repr(e)}")
        raise BackendAPIError(
            status_code=502,
            message=client_error_detail(
                f"Credential resolution failed: {e}",
                "The proxy could not authenticate to the backend service.",
            ),
            error_type="credential_error",
            client_format=ClientFormat.ANTHROPIC,
            provider="anthropic",
        ) from e
    elif isinstance(e, AnthropicStatusError):
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
        logger.warning(f"[{call_id}] Anthropic connection error: {repr(e)}")
        raise BackendAPIError(
            status_code=502,
            message=client_error_detail(str(e), "An error occurred while connecting to the API."),
            error_type="api_connection_error",
            client_format=ClientFormat.ANTHROPIC,
            provider="anthropic",
        ) from e
    # For other exceptions, let them propagate


__all__ = ["process_anthropic_request", "_reconstruct_response_from_stream_events"]
