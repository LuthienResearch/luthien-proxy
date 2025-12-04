"""Anthropic client formatter implementation."""

import asyncio
import json
import logging

from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.client_formatter.anthropic_sse_assembler import AnthropicSSEAssembler
from luthien_proxy.streaming.client_formatter.interface import ClientFormatter

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Queue put timeout to prevent deadlock if client is slow
QUEUE_PUT_TIMEOUT = 30.0


class AnthropicClientFormatter(ClientFormatter):
    """Converts common format chunks to Anthropic SSE events."""

    def __init__(self, model_name: str):
        """Initialize formatter with model name for message_start event.

        Args:
            model_name: Model name to include in message_start event (required, no default)
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
        """Convert common format chunks to Anthropic SSE format.

        Reads chunks from input queue, converts them to Anthropic-specific
        SSE events, and writes to output queue.

        Args:
            input_queue: Queue to read chunks from
            output_queue: Queue to write SSE events to
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        with tracer.start_as_current_span("streaming.client_formatter") as span:
            span.set_attribute("formatter.type", "anthropic")
            span.set_attribute("formatter.model", self.model_name)
            chunk_count = 0

            message_started = False
            assembler = AnthropicSSEAssembler()

            try:
                while True:
                    chunk = await input_queue.get()

                    # None signals end of stream
                    if chunk is None:
                        break

                    chunk_count += 1

                    # Send message_start before first chunk
                    if not message_started:
                        message_started = True
                        message_start = {
                            "type": "message_start",
                            "message": {
                                "id": f"msg_{policy_ctx.transaction_id}",
                                "type": "message",
                                "role": "assistant",
                                "content": [],
                                "model": self.model_name,
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                            },
                        }
                        sse_line = f"event: message_start\ndata: {json.dumps(message_start)}\n\n"
                        await self._safe_put(output_queue, sse_line)

                    # Convert chunk to Anthropic events using stateful assembler
                    events = assembler.process_chunk(chunk)

                    # Emit all events in Anthropic SSE format: "event: <type>\ndata: <json>\n\n"
                    for event in events:
                        event_type = event.get("type", "content_block_delta")
                        json_str = json.dumps(event)
                        sse_line = f"event: {event_type}\ndata: {json_str}\n\n"
                        await self._safe_put(output_queue, sse_line)

                # Send message_stop at end (only if we started)
                if message_started:
                    message_stop = {"type": "message_stop"}
                    sse_line = f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"
                    logger.info(f"[ClientFormatter] Sending message_stop: {repr(sse_line)}")
                    await self._safe_put(output_queue, sse_line)

                span.set_attribute("formatter.chunk_count", chunk_count)
            finally:
                # Signal end of stream to output queue
                await self._safe_put(output_queue, None)


__all__ = ["AnthropicClientFormatter"]
