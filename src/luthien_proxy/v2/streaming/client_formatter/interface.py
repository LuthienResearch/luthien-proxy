# ABOUTME: ClientFormatter interface - converts common format to client-specific SSE
# ABOUTME: Implemented by OpenAI and Anthropic formatters

"""Client formatter interface for streaming responses."""

import asyncio
from typing import Any, Protocol

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class ClientFormatter(Protocol):
    """Converts common format chunks to client-specific SSE events.

    Implementations consume common-format chunks (from policy egress)
    and produce client-specific SSE events (OpenAI or Anthropic).
    """

    async def process(
        self,
        input_queue: asyncio.Queue[Any],  # Common format chunks
        output_queue: asyncio.Queue[Any],  # Client-specific SSE events
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert common format chunks to client SSE format.

        Reads common-format chunks from input_queue, converts them to
        client-specific SSE events, and writes to output_queue.

        Args:
            input_queue: Queue of common format chunks
            output_queue: Queue for client-specific SSE events
            policy_ctx: Policy context (typically unused in formatter)
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        ...


__all__ = ["ClientFormatter"]
