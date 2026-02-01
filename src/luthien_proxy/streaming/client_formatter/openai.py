"""OpenAI client formatter implementation."""

import asyncio
import json
import logging

from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.client_formatter.interface import ClientFormatter
from luthien_proxy.utils.constants import QUEUE_PUT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class OpenAIClientFormatter(ClientFormatter):
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
            await asyncio.wait_for(queue.put(item), timeout=QUEUE_PUT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.error(f"Queue put timeout after {QUEUE_PUT_TIMEOUT_SECONDS}s - client may be slow or disconnected")
            raise

    async def process(
        self,
        input_queue: asyncio.Queue[ModelResponse | None],
        output_queue: asyncio.Queue[str | None],
        policy_ctx: PolicyContext,
    ) -> None:
        """Convert common format chunks to OpenAI SSE format.

        Reads chunks from input queue, converts them to OpenAI-specific
        SSE events, and writes to output queue.

        Args:
            input_queue: Queue to read chunks from
            output_queue: Queue to write SSE events to
            policy_ctx: Policy context for shared state

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        with tracer.start_as_current_span("streaming.client_formatter") as span:
            span.set_attribute("formatter.type", "openai")
            span.set_attribute("formatter.model", self.model_name)
            chunk_count = 0

            try:
                while True:
                    chunk = await input_queue.get()

                    # None signals end of stream
                    if chunk is None:
                        break

                    chunk_count += 1
                    # Convert ModelResponse to SSE format: "data: {json}\n\n"
                    chunk_dict = chunk.model_dump()
                    # Remove LiteLLM-specific fields that Codex may not tolerate in chat SSE.
                    # We do post-hoc deletion to also strip nested delta fields.
                    chunk_dict.pop("provider_specific_fields", None)
                    chunk_dict.pop("citations", None)
                    chunk_dict.pop("obfuscation", None)
                    for choice in chunk_dict.get("choices", []):
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            delta.pop("provider_specific_fields", None)
                    sse_line = f"data: {json.dumps(chunk_dict)}\n\n"
                    await self._safe_put(output_queue, sse_line)

                # Send [DONE] marker per OpenAI streaming spec
                await self._safe_put(output_queue, "data: [DONE]\n\n")
                span.set_attribute("formatter.chunk_count", chunk_count)
            finally:
                # Signal end of stream to output queue
                await self._safe_put(output_queue, None)


__all__ = ["OpenAIClientFormatter"]
