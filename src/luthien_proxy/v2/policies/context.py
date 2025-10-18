# ABOUTME: Context object passed to policy methods for event emission
# ABOUTME: Provides call_id and event emission without polluting policy state

"""Policy execution context.

This module defines PolicyContext, which carries everything a policy needs
beyond the message itself (call_id, event emitter, metadata).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from luthien_proxy.v2.control.models import PolicyEvent


class PolicyContext:
    """Context provided to policy methods for event emission and correlation.

    This carries everything a policy needs that's not part of the message itself:
    - Call ID for correlation across the request/response lifecycle
    - Event emitter for logging policy decisions
    - Any other per-request metadata

    Policies remain stateless - all per-request state lives in this context.
    """

    def __init__(
        self,
        call_id: str,
        emit_event: Callable[[PolicyEvent], None],
    ):
        """Initialize policy context.

        Args:
            call_id: Unique identifier for this request/response cycle
            emit_event: Callback to emit policy events
        """
        self.call_id = call_id
        self._emit_event = emit_event

    def emit(
        self,
        event_type: str,
        summary: str,
        details: Optional[dict[str, Any]] = None,
        severity: str = "info",
    ) -> None:
        """Emit a policy event.

        Args:
            event_type: Type of event (e.g., 'request_modified', 'content_filtered')
            summary: Human-readable summary of what happened
            details: Additional structured data about the event
            severity: Severity level: debug, info, warning, error
        """
        from luthien_proxy.v2.control.models import PolicyEvent

        event = PolicyEvent(
            event_type=event_type,
            call_id=self.call_id,
            summary=summary,
            details=details or {},
            severity=severity,
        )
        self._emit_event(event)


__all__ = ["PolicyContext"]
