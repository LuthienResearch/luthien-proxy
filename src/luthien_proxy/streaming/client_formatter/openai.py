# ABOUTME: OpenAI implementation of ClientFormatter
# ABOUTME: Converts common format chunks to OpenAI SSE events

"""OpenAI client formatter implementation."""

import asyncio
import logging

from litellm.types.utils import ModelResponse

from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

# Queue put timeout to prevent deadlock if client is slow
QUEUE_PUT_TIMEOUT = 30.0


class OpenAIClientFormatter:
    """Converts common format chunks to OpenAI SSE events."""

    def __init__(self, model_name: str):
        """Initialize formatter with model name.

        Args:
            model_name: Model name for the request (required, no default)
        """
        self.model_name = model_name

    async def _safe_put(self, queue: asyncio.Queue[str | None], item: str | None) -> None:
        """Safely put item in queue with timeout to prevent deadlock.

        Args:
            queue: Queue to put item into
            item: Item to put

        Raises:
            asyncio.TimeoutError: If queue is full and timeout is exceeded
        """
        try:
            await asyncio.wait_for(queue.put(item), timeout=QUEUE_PUT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"Queue put timeout after {QUEUE_PUT_TIMEOUT}s - client may be slow or disconnected")
            raise

    async def process(
        self,
        input_queue: asyncio.Queue[ModelResponse | None],
        output_queue: asyncio.Queue[str | None],
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
        try:
            while True:
                chunk = await input_queue.get()

                # None signals end of stream
                if chunk is None:
                    break

                # Convert ModelResponse to SSE format: "data: {json}\n\n"
                sse_line = f"data: {chunk.model_dump_json()}\n\n"
                await self._safe_put(output_queue, sse_line)

            # Send [DONE] marker per OpenAI streaming spec
            await self._safe_put(output_queue, "data: [DONE]\n\n")
        finally:
            # Signal end of stream to output queue
            await self._safe_put(output_queue, None)


__all__ = ["OpenAIClientFormatter"]
