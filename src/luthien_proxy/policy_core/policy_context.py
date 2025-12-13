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
from luthien_proxy.types import RawHttpRequest

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
