"""Policy context for the streaming pipeline.

This module defines PolicyContext, which provides shared mutable state
that persists across the entire request/response lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from luthien_proxy.observability.emitter import (
    EventEmitterProtocol,
    NullEventEmitter,
)

if TYPE_CHECKING:
    from luthien_proxy.messages import Request


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
    ) -> None:
        """Initialize policy context for a request.

        Args:
            transaction_id: Unique identifier for this request/response cycle
            request: Optional original request for policies that need it
            emitter: Event emitter for recording observability events.
                     If not provided, a NullEventEmitter is used.
        """
        self.transaction_id: str = transaction_id
        self.request: "Request | None" = request
        self._emitter: EventEmitterProtocol = emitter or NullEventEmitter()
        self._scratchpad: dict[str, Any] = {}

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

    @classmethod
    def for_testing(
        cls,
        transaction_id: str = "test-txn",
        request: "Request | None" = None,
    ) -> "PolicyContext":
        """Create a PolicyContext suitable for unit tests.

        Uses NullEventEmitter so no external dependencies are required.

        Args:
            transaction_id: Transaction ID (defaults to "test-txn")
            request: Optional request object

        Returns:
            PolicyContext with null implementations for external services
        """
        return cls(
            transaction_id=transaction_id,
            request=request,
            emitter=NullEventEmitter(),
        )


__all__ = ["PolicyContext"]
