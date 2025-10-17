# ABOUTME: Base class for V2 policies - reactive streaming with explicit message types
# ABOUTME: Policies are tasks that build responses by reacting to incoming information

"""Policy handler base class for V2 architecture.

Policies process three message types:
1. Request - transform/validate requests before sending to LLM
2. FullResponse - transform/validate complete responses
3. StreamingResponse - reactive task that builds output based on incoming chunks

Policies emit PolicyEvents to describe their activity (for logging, UI, debugging).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Optional

from luthien_proxy.v2.control.models import PolicyEvent
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.streaming import ChunkQueue

if TYPE_CHECKING:
    from typing import Any, Callable

    PolicyEventHandler = Callable[[PolicyEvent], None]


class PolicyHandler(ABC):
    """Base class for policy handlers.

    Override these methods to implement custom policies:
    - process_request: Transform/validate requests before sending to LLM
    - process_full_response: Transform/validate complete responses
    - process_streaming_response: Reactive task that builds output stream
    """

    def __init__(self):
        """Initialize policy handler."""
        self._event_handler: Optional[PolicyEventHandler] = None
        self._call_id: Optional[str] = None

    def set_event_handler(self, handler: PolicyEventHandler) -> None:
        """Set the event handler for emitting policy events."""
        self._event_handler = handler

    def set_call_id(self, call_id: str) -> None:
        """Set the current call ID for event emission."""
        self._call_id = call_id

    def emit_event(
        self,
        event_type: str,
        summary: str,
        details: Optional[dict[str, Any]] = None,
        severity: str = "info",
    ) -> None:
        """Emit a policy event."""
        if self._event_handler and self._call_id:
            event = PolicyEvent(
                event_type=event_type,
                call_id=self._call_id,
                summary=summary,
                details=details or {},
                severity=severity,
            )
            self._event_handler(event)

    @abstractmethod
    async def process_request(self, request: Request) -> Request:
        """Process a request before sending to LLM."""
        pass

    @abstractmethod
    async def process_full_response(self, response: FullResponse) -> FullResponse:
        """Process a complete (non-streaming) response."""
        pass

    @abstractmethod
    async def process_streaming_response(
        self,
        incoming: ChunkQueue[StreamingResponse],
        outgoing: ChunkQueue[StreamingResponse],
        keepalive: Optional[Callable[[], None]] = None,
    ) -> None:
        """Reactive streaming task: build output response based on incoming chunks.

        Read chunks from incoming queue, process them, and write to outgoing queue.
        Call keepalive() periodically during long-running operations to prevent timeout.
        Always close outgoing queue in a finally block when done.
        """
        pass


__all__ = ["PolicyHandler"]
