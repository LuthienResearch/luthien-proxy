# ABOUTME: Base class for V2 policies - stateless, functional design
# ABOUTME: Policies process messages and emit events via provided context

"""Policy base class for V2 architecture.

Policies process:
1. Request - transform/validate requests before sending to LLM
2. ModelResponse - transform/validate complete responses (non-streaming)
3. ModelResponse chunks - reactive task that builds output based on incoming chunks

Policies are stateless - all per-request state is passed via PolicyContext.
Policies emit events via context.emit() to describe their activity.
"""

from __future__ import annotations

import asyncio
from abc import ABC
from typing import TYPE_CHECKING, Callable

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.streaming import get_available

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

    Default implementations pass data through unchanged, so you only need
    to override the methods relevant to your policy.
    """

    async def process_request(
        self,
        request: Request,
        context: PolicyContext,
    ) -> Request:
        """Process a request before sending to LLM.

        Default implementation: pass through unchanged.

        Args:
            request: The request to process
            context: Context for emitting events and accessing call metadata

        Returns:
            Transformed request

        Raises:
            Exception: To reject the request
        """
        return request

    async def process_full_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process a complete (non-streaming) response.

        Default implementation: pass through unchanged.

        Args:
            response: The ModelResponse from LiteLLM to process
            context: Context for emitting events and accessing call metadata

        Returns:
            Transformed ModelResponse
        """
        return response

    async def process_streaming_response(
        self,
        incoming: asyncio.Queue[ModelResponse],
        outgoing: asyncio.Queue[ModelResponse],
        context: PolicyContext,
        keepalive: Callable[[], None] | None = None,
    ) -> None:
        """Reactive streaming task: build output response based on incoming chunks.

        Default implementation: forward all chunks from incoming to outgoing.

        Read chunks from incoming queue, process them, and write to outgoing queue.
        Use get_available(incoming) to get batches of chunks.
        Call keepalive() periodically during long-running operations to prevent timeout.
        Always shut down outgoing queue with outgoing.shutdown() in a finally block when done.

        Args:
            incoming: Queue of ModelResponse chunks from LLM (shut down to signal end)
            outgoing: Queue of ModelResponse chunks to send to client (shut down when done)
            context: Context for emitting events and accessing call metadata
            keepalive: Optional callback to prevent timeout during slow processing
        """
        try:
            while True:
                chunks = await get_available(incoming)
                if not chunks:
                    break
                for chunk in chunks:
                    await outgoing.put(chunk)
        finally:
            outgoing.shutdown()


__all__ = ["LuthienPolicy"]
