# ABOUTME: OpenAI implementation of ClientFormatter
# ABOUTME: Converts common format chunks to OpenAI SSE events

"""OpenAI client formatter implementation."""

import asyncio

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class OpenAIClientFormatter:
    """Converts common format chunks to OpenAI SSE events."""

    async def process(
        self,
        input_queue: asyncio.Queue[ModelResponse],
        output_queue: asyncio.Queue[str],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert common format chunks to OpenAI SSE format.

        Reads chunks from input queue, converts them to OpenAI-specific
        SSE events, and writes to output queue.

        Args:
            input_queue: Queue to read chunks from
            output_queue: Queue to write SSE events to
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        while True:
            chunk = await input_queue.get()

            # None signals end of stream
            if chunk is None:
                break

            # Convert ModelResponse to SSE format: "data: {json}\n\n"
            sse_line = f"data: {chunk.model_dump_json()}\n\n"
            await output_queue.put(sse_line)


__all__ = ["OpenAIClientFormatter"]
