# ABOUTME: Base class for V2 policies - stateless, functional design
# ABOUTME: Policies process messages and emit events via provided context

"""Policy base class for V2 architecture.

Policies process three message types:
1. Request - transform/validate requests before sending to LLM
2. FullResponse - transform/validate complete responses
3. StreamingResponse - reactive task that builds output based on incoming chunks

Policies are stateless - all per-request state is passed via PolicyContext.
Policies emit events via context.emit() to describe their activity.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Optional

from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.streaming import ChunkQueue

if TYPE_CHECKING:
    pass


class LuthienPolicy(ABC):
    """Base class for Luthien policies - stateless and functional.

    Policies receive:
    - The message to process (Request/FullResponse/StreamingResponse)
    - A PolicyContext for emitting events and accessing call metadata

    Policies return:
    - Transformed message (or raise exception to reject)

    No mutable state. No side effects except event emission via context.

    Override these methods to implement custom policies:
    - process_request: Transform/validate requests before sending to LLM
    - process_full_response: Transform/validate complete responses
    - process_streaming_response: Reactive task that builds output stream
    """

    @abstractmethod
    async def process_request(
        self,
        request: Request,
        context: PolicyContext,
    ) -> Request:
        """Process a request before sending to LLM.

        Args:
            request: The request to process
            context: Context for emitting events and accessing call metadata

        Returns:
            Transformed request

        Raises:
            Exception: To reject the request
        """
        pass

    @abstractmethod
    async def process_full_response(
        self,
        response: FullResponse,
        context: PolicyContext,
    ) -> FullResponse:
        """Process a complete (non-streaming) response.

        Args:
            response: The response to process
            context: Context for emitting events and accessing call metadata

        Returns:
            Transformed response
        """
        pass

    @abstractmethod
    async def process_streaming_response(
        self,
        incoming: ChunkQueue[StreamingResponse],
        outgoing: ChunkQueue[StreamingResponse],
        context: PolicyContext,
        keepalive: Optional[Callable[[], None]] = None,
    ) -> None:
        """Reactive streaming task: build output response based on incoming chunks.

        Read chunks from incoming queue, process them, and write to outgoing queue.
        Call keepalive() periodically during long-running operations to prevent timeout.
        Always close outgoing queue in a finally block when done.

        Args:
            incoming: Queue of chunks from LLM
            outgoing: Queue of chunks to send to client
            context: Context for emitting events and accessing call metadata
            keepalive: Optional callback to prevent timeout during slow processing
        """
        pass


__all__ = ["LuthienPolicy"]
