# ABOUTME: No-op policy implementation - passes everything through unchanged
# ABOUTME: Useful for testing and as a base for development

"""No-op policy that passes all messages through unchanged."""

from __future__ import annotations

import asyncio
from typing import Callable

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.streaming import get_available


class NoOpPolicy(LuthienPolicy):
    """Policy that does nothing - passes everything through unchanged.

    Useful for:
    - Testing the proxy without policy interference
    - Baseline performance measurements
    - Development and debugging
    """

    async def process_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass request through unchanged."""
        return request

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Pass response through unchanged."""
        return response

    async def process_streaming_response(
        self,
        incoming: asyncio.Queue[ModelResponse],
        outgoing: asyncio.Queue[ModelResponse],
        context: PolicyContext,
        keepalive: Callable[[], None] | None = None,
    ) -> None:
        """Pass all streaming chunks through unchanged."""
        try:
            while True:
                # Get all available chunks
                batch = await get_available(incoming)
                if not batch:  # Stream ended
                    break

                # Forward all chunks unchanged
                for chunk in batch:
                    await outgoing.put(chunk)
        finally:
            # Always shut down outgoing queue when done
            outgoing.shutdown()


__all__ = ["NoOpPolicy"]
