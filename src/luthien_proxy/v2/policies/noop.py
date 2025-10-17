# ABOUTME: No-op policy implementation - passes everything through unchanged
# ABOUTME: Useful for testing and as a base for development

"""No-op policy that passes all messages through unchanged."""

from __future__ import annotations

from typing import Callable, Optional

from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.base import PolicyHandler
from luthien_proxy.v2.streaming import ChunkQueue


class NoOpPolicy(PolicyHandler):
    """Policy that does nothing - passes everything through unchanged.

    Useful for:
    - Testing the proxy without policy interference
    - Baseline performance measurements
    - Development and debugging
    """

    def __init__(self):
        """Initialize no-op policy."""
        super().__init__()

    async def process_request(self, request: Request) -> Request:
        """Pass request through unchanged."""
        return request

    async def process_full_response(self, response: FullResponse) -> FullResponse:
        """Pass response through unchanged."""
        return response

    async def process_streaming_response(
        self,
        incoming: ChunkQueue[StreamingResponse],
        outgoing: ChunkQueue[StreamingResponse],
        keepalive: Optional[Callable[[], None]] = None,
    ) -> None:
        """Pass all streaming chunks through unchanged."""
        try:
            while True:
                # Get all available chunks
                batch = await incoming.get_available()
                if not batch:  # Stream ended
                    break

                # Forward all chunks unchanged
                for chunk in batch:
                    await outgoing.put(chunk)
        finally:
            # Always close outgoing queue when done
            await outgoing.close()


__all__ = ["NoOpPolicy"]
