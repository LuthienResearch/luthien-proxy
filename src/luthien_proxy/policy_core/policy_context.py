# ABOUTME: PolicyContext for cross-stage state management
# ABOUTME: Shared mutable state across request/response lifecycle

"""Policy context for the streaming pipeline.

This module defines PolicyContext, which provides shared mutable state
that persists across the entire request/response lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from luthien_proxy.observability.context import NoOpObservabilityContext

if TYPE_CHECKING:
    from luthien_proxy.messages import Request
    from luthien_proxy.observability.context import ObservabilityContext


class PolicyContext:
    """Shared mutable state across the entire request/response lifecycle.

    This context is created at the gateway level and passed through both
    request processing and streaming response processing. It provides
    cross-stage state storage via a scratchpad dictionary.

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
        request: Request | None = None,
        observability: ObservabilityContext | None = None,
    ) -> None:
        """Initialize policy context for a request.

        Args:
            transaction_id: Unique identifier for this request/response cycle
            observability: Optional observability context for logging/tracing (default to NoOpObservabilityContext)
            request: Optional original request for policies that need it
        """
        self.transaction_id: str = transaction_id
        self.request: Request | None = request
        self.observability: ObservabilityContext = observability or NoOpObservabilityContext(
            transaction_id=transaction_id
        )
        self._scratchpad: dict[str, Any] = {}

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


__all__ = ["PolicyContext"]
