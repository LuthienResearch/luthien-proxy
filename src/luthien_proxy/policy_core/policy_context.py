"""Policy context for the streaming pipeline.

This module defines PolicyContext, which provides shared mutable state
that persists across the entire request/response lifecycle.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

from opentelemetry import trace

from luthien_proxy.observability.emitter import (
    EventEmitterProtocol,
    NullEventEmitter,
)
from luthien_proxy.types import RawHttpRequest

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from luthien_proxy.messages import Request

_tracer = trace.get_tracer(__name__)


class PolicyContext:
    """Shared mutable state across the entire request/response lifecycle.

    This context is created at the gateway level and passed through both
    request processing and streaming response processing. It provides
    cross-stage state storage via a scratchpad dictionary and access to
    the event emitter for recording observability events.

    Policies can use the scratchpad to:
    - Track whether safety checks have been performed
    - Store intermediate results from trusted monitors
    - Accumulate metrics across streaming chunks
    - Share any state between request and response processing

    The context is NOT thread-safe and should only be accessed from async
    code within a single request handler.
    """

    def __init__(
        self,
        transaction_id: str,
        request: "Request | None" = None,
        emitter: EventEmitterProtocol | None = None,
        raw_http_request: RawHttpRequest | None = None,
        session_id: str | None = None,
    ) -> None:
        """Initialize policy context for a request.

        Args:
            transaction_id: Unique identifier for this request/response cycle
            request: Optional original request for policies that need it (OpenAI format)
            emitter: Event emitter for recording observability events.
                     If not provided, a NullEventEmitter is used.
            raw_http_request: Optional raw HTTP request data before any processing.
                              Contains original headers, body, method, and path.
            session_id: Optional session identifier extracted from client request.
                        For Anthropic clients, extracted from metadata.user_id.
                        For OpenAI clients, extracted from x-session-id header.
        """
        self.transaction_id: str = transaction_id
        self.request: "Request | None" = request
        self.raw_http_request: RawHttpRequest | None = raw_http_request
        self.session_id: str | None = session_id
        self._emitter: EventEmitterProtocol = emitter or NullEventEmitter()
        self._scratchpad: dict[str, Any] = {}

        # Policy summaries - optional human-readable descriptions of what the policy did.
        # These are set by policies and propagated to span attributes for observability.
        self.request_summary: str | None = None
        self.response_summary: str | None = None

    @property
    def emitter(self) -> EventEmitterProtocol:
        """Event emitter for recording observability events.

        Use this to record events from policies without depending on globals.
        Events are recorded fire-and-forget style.

        Example:
            ctx.emitter.record(ctx.transaction_id, "policy.decision", {"action": "allow"})
        """
        return self._emitter

    @property
    def scratchpad(self) -> dict[str, Any]:
        """Mutable dictionary for storing arbitrary policy state.

        Policies can use this to share state across invocations. For example:
        - Track whether a safety check has been performed
        - Store intermediate results from trusted monitors
        - Accumulate metrics across streaming chunks

        Returns:
            Mutable dictionary unique to this context
        """
        return self._scratchpad

    def record_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Convenience method to record an event for this transaction.

        This is a shorthand for ctx.emitter.record(ctx.transaction_id, ...).

        Args:
            event_type: Type of event (e.g., "policy.modified_request")
            data: Event payload
        """
        self._emitter.record(self.transaction_id, event_type, data)

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator["Span"]:
        """Create a child span for policy operations.

        Use this to create nested spans within policy hooks for detailed
        observability. Spans created here will appear as children of the
        current span (typically process_response or policy_on_request).

        The span name is automatically prefixed with "policy." to distinguish
        policy spans from infrastructure spans.

        Args:
            name: Span name (will be prefixed with "policy.")
            attributes: Optional span attributes to set

        Yields:
            The created span for adding events or attributes

        Example:
            async def on_content_complete(self, ctx: StreamingPolicyContext):
                with ctx.policy_ctx.span("check_safety") as span:
                    result = await self._run_safety_check(ctx)
                    span.set_attribute("policy.check_passed", result.passed)
                    if not result.passed:
                        span.add_event("policy.content_blocked", {"reason": result.reason})
        """
        span_name = f"policy.{name}" if not name.startswith("policy.") else name
        with _tracer.start_as_current_span(span_name) as span:
            span.set_attribute("luthien.transaction_id", self.transaction_id)
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
            yield span

    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add an event to the current span.

        Use this for point-in-time events that don't need their own span.
        Events are lightweight and don't add span overhead.

        Args:
            name: Event name (e.g., "policy.content_filtered")
            attributes: Optional event attributes

        Example:
            ctx.add_span_event("policy.sql_detected", {"pattern": "DROP TABLE"})
        """
        current_span = trace.get_current_span()
        if current_span.is_recording():
            current_span.add_event(name, attributes=attributes or {})

    @classmethod
    def for_testing(
        cls,
        transaction_id: str = "test-txn",
        request: "Request | None" = None,
        raw_http_request: RawHttpRequest | None = None,
        session_id: str | None = None,
    ) -> "PolicyContext":
        """Create a PolicyContext suitable for unit tests.

        Uses NullEventEmitter so no external dependencies are required.

        Args:
            transaction_id: Transaction ID (defaults to "test-txn")
            request: Optional request object
            raw_http_request: Optional raw HTTP request data
            session_id: Optional session ID

        Returns:
            PolicyContext with null implementations for external services
        """
        return cls(
            transaction_id=transaction_id,
            request=request,
            emitter=NullEventEmitter(),
            raw_http_request=raw_http_request,
            session_id=session_id,
        )


__all__ = ["PolicyContext"]
