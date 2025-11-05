# ABOUTME: ClientFormatter converts common format chunks to client-specific SSE events
# ABOUTME: Separate implementations for OpenAI and Anthropic client formats

"""Client format converters for streaming responses.

This module provides StreamProcessor implementations that convert
common-format chunks into client-specific SSE events (OpenAI or Anthropic).
"""

import asyncio
from typing import Any

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class OpenAIClientFormatter:
    """Converts common format chunks to OpenAI SSE events.

    Consumes common-format chunks (from policy egress) and produces
    OpenAI-compatible SSE events for streaming to the client.
    """

    async def process(
        self,
        input_queue: asyncio.Queue[Any],  # Common format chunks
        output_queue: asyncio.Queue[Any],  # OpenAI SSE events
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert common format chunks to OpenAI SSE format.

        Reads common-format chunks from input_queue, converts them to
        OpenAI-specific SSE events, and writes to output_queue.

        Args:
            input_queue: Queue of common format chunks
            output_queue: Queue for OpenAI SSE events
            policy_ctx: Policy context (unused in formatter)
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        pass  # TODO: Implement


class AnthropicClientFormatter:
    """Converts common format chunks to Anthropic SSE events.

    Consumes common-format chunks (from policy egress) and produces
    Anthropic-compatible SSE events for streaming to the client.
    """

    async def process(
        self,
        input_queue: asyncio.Queue[Any],  # Common format chunks
        output_queue: asyncio.Queue[Any],  # Anthropic SSE events
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert common format chunks to Anthropic SSE format.

        Reads common-format chunks from input_queue, converts them to
        Anthropic-specific SSE events, and writes to output_queue.

        Args:
            input_queue: Queue of common format chunks
            output_queue: Queue for Anthropic SSE events
            policy_ctx: Policy context (unused in formatter)
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        pass  # TODO: Implement


__all__ = ["OpenAIClientFormatter", "AnthropicClientFormatter"]
