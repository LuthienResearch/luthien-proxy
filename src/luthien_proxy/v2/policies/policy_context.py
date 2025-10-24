# ABOUTME: Context object passed to policy methods for event emission
# ABOUTME: Provides call_id and OpenTelemetry span for tracing

"""Policy execution context.

This module defines PolicyContext, which carries everything a policy needs
beyond the message itself (call_id, OpenTelemetry span for tracing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.observability import RedisEventPublisher


class PolicyContext:
    """Context provided to policy methods for event emission and correlation.

    This carries everything a policy needs that's not part of the message itself:
    - Call ID for correlation across the request/response lifecycle
    - OpenTelemetry span for distributed tracing
    - Optional event publisher for real-time UI updates
    - Original request (added in V3)
    - Per-request scratchpad for policy-specific state (added in V3)

    Policies remain stateless - all per-request state lives in this context.
    """

    call_id: str
    """Unique identifier for this request-response cycle."""

    span: Span
    """OpenTelemetry span for tracing."""

    request: Request
    """Original request from client (added in V3)."""

    scratchpad: dict[str, Any]
    """Per-request scratchpad for policy-specific state.

    Policies can store arbitrary data here without needing to subclass PolicyContext.
    Common uses:
    - Counters: scratchpad['tool_calls_judged'] = 0
    - Flags: scratchpad['already_warned'] = True
    - Buffers: scratchpad['buffered_text'] = []
    - Metadata: scratchpad['block_reason'] = "harmful tool call"

    Each request gets a fresh empty scratchpad. Data does not persist across requests.
    """

    _event_publisher: RedisEventPublisher | None
    """Optional publisher for real-time UI events."""

    def __init__(
        self,
        call_id: str,
        span: Span,
        request: Request,
        event_publisher: RedisEventPublisher | None = None,
    ):
        """Initialize policy context.

        Args:
            call_id: Unique identifier for this request/response cycle
            span: OpenTelemetry span for this policy execution
            request: Original request from client
            event_publisher: Optional publisher for real-time UI events
        """
        self.call_id = call_id
        self.span = span
        self.request = request
        self.scratchpad = {}  # Fresh empty dict per request
        self._event_publisher = event_publisher

    def emit(
        self,
        event_type: str,
        summary: str,
        details: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        """Emit a policy event as an OpenTelemetry span event.

        This adds an event to the current span and optionally publishes to Redis
        for real-time UI monitoring.

        Args:
            event_type: Type of event (e.g., 'policy.request_modified', 'policy.content_filtered')
            summary: Human-readable summary of what happened
            details: Additional structured data about the event
            severity: Severity level: debug, info, warning, error
        """
        # Add event to OpenTelemetry span
        attributes = {
            "event.type": event_type,
            "event.summary": summary,
            "event.severity": severity,
        }

        # Add details as individual attributes
        if details:
            for key, value in details.items():
                # OTel attributes must be primitives
                if isinstance(value, (str, int, float, bool)):
                    attributes[f"event.{key}"] = value  # type: ignore[assignment]
                else:
                    # Convert complex types to string
                    attributes[f"event.{key}"] = str(value)

        self.span.add_event(event_type, attributes=attributes)

        # Optionally publish to Redis for real-time UI
        if self._event_publisher:
            import asyncio

            # If we're in an async context, schedule the publish
            # (don't await - fire and forget for performance)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        self._event_publisher.publish_event(
                            call_id=self.call_id,
                            event_type=event_type,
                            data={"summary": summary, "severity": severity, **(details or {})},
                        )
                    )
            except RuntimeError:
                # No event loop - skip real-time publish
                pass


__all__ = ["PolicyContext"]
