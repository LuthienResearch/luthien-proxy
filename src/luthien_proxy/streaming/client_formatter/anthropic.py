# ABOUTME: Anthropic implementation of ClientFormatter
# ABOUTME: Converts common format chunks to Anthropic SSE events

"""Anthropic client formatter implementation."""

import asyncio
import json
import logging

from litellm.types.utils import ModelResponse

from luthien_proxy.llm.anthropic_sse_assembler import AnthropicSSEAssembler
from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

# Queue put timeout to prevent deadlock if client is slow
QUEUE_PUT_TIMEOUT = 30.0


class AnthropicClientFormatter:
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
        message_started = False
        assembler = AnthropicSSEAssembler()

        try:
            while True:
                chunk = await input_queue.get()

                # None signals end of stream
                if chunk is None:
                    break

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
                    # logger.info(f"[ClientFormatter] Sending message_start: {repr(sse_line[:200])}")
                    await self._safe_put(output_queue, sse_line)

                # Convert chunk to Anthropic events using stateful assembler
                events = assembler.process_chunk(chunk)

                # Emit all events in Anthropic SSE format: "event: <type>\ndata: <json>\n\n"
                for event in events:
                    # event_type = event.get("type", "content_block_delta")
                    # json_str = json.dumps(event)
                    # sse_line = f"event: {event_type}\ndata: {json_str}\n\n"

                    # Comprehensive logging for debugging
                    # logger.info(f"[ClientFormatter] Event type: {event_type}")
                    # logger.info(f"[ClientFormatter] JSON string length: {len(json_str)}")
                    # logger.info(f"[ClientFormatter] JSON first 100 chars: {json_str[:100]}")
                    # logger.info(f"[ClientFormatter] JSON last 100 chars: {json_str[-100:]}")

                    # Validate JSON is parseable
                    # try:
                    #     json.loads(json_str)
                    #     logger.info("[ClientFormatter] ✓ JSON is valid")
                    # except json.JSONDecodeError as e:
                    #     logger.error(f"[ClientFormatter] ✗ JSON PARSE ERROR: {e}")
                    #     logger.error(f"[ClientFormatter] Full JSON: {json_str}")

                    # Log the actual SSE line to check format
                    # logger.info(f"[ClientFormatter] SSE line (repr): {repr(sse_line[:300])}")
                    await self._safe_put(output_queue, sse_line)

            # Send message_stop at end (only if we started)
            if message_started:
                message_stop = {"type": "message_stop"}
                sse_line = f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"
                logger.info(f"[ClientFormatter] Sending message_stop: {repr(sse_line)}")
                await self._safe_put(output_queue, sse_line)
        finally:
            # Signal end of stream to output queue
            await self._safe_put(output_queue, None)


__all__ = ["AnthropicClientFormatter"]
