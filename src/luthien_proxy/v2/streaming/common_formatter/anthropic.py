# ABOUTME: Anthropic implementation of CommonFormatter
# ABOUTME: Converts Anthropic streaming chunks to common format

"""Anthropic common formatter implementation."""

import asyncio
from typing import Any

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class AnthropicCommonFormatter:
    """Converts Anthropic streaming chunks to common format."""

    async def process(
        self,
        input_stream: Any,  # Anthropic stream (AsyncIterator)
        output_queue: asyncio.Queue[Any],  # Common format chunks
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert Anthropic chunks to common format.

        Reads Anthropic-specific streaming chunks, converts them to common
        format, and writes to output queue.

        Args:
            input_stream: Stream of Anthropic chunks to convert
            output_queue: Queue to write converted chunks to
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        pass  # TODO: Implement


__all__ = ["AnthropicCommonFormatter"]
