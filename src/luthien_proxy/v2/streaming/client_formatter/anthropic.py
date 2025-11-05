# ABOUTME: Anthropic implementation of ClientFormatter
# ABOUTME: Converts common format chunks to Anthropic SSE events

"""Anthropic client formatter implementation."""

import asyncio
import json

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.llm.anthropic_sse_assembler import AnthropicSSEAssembler
from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class AnthropicClientFormatter:
    """Converts common format chunks to Anthropic SSE events."""

    def __init__(self, model_name: str = "claude-3-opus-20240229"):
        """Initialize formatter with model name for message_start event.

        Args:
            model_name: Model name to include in message_start event
        """
        self.model_name = model_name

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
                    await output_queue.put(sse_line)

                # Convert chunk to Anthropic events using stateful assembler
                events = assembler.process_chunk(chunk)

                # Emit all events in Anthropic SSE format: "event: <type>\ndata: <json>\n\n"
                for event in events:
                    event_type = event.get("type", "content_block_delta")
                    sse_line = f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
                    await output_queue.put(sse_line)

            # Send message_stop at end (only if we started)
            if message_started:
                message_stop = {"type": "message_stop"}
                sse_line = f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"
                await output_queue.put(sse_line)
        finally:
            # Signal end of stream to output queue
            await output_queue.put(None)


__all__ = ["AnthropicClientFormatter"]
