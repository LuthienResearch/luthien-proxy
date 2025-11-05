# ABOUTME: CommonFormatter converts backend-specific chunks to common format
# ABOUTME: Separate implementations for OpenAI and Anthropic streaming formats

"""Common format converters for backend streaming responses.

This module provides StreamProcessor implementations that convert
backend-specific streaming chunks (OpenAI, Anthropic) into our
common chunk format for policy processing.
"""

import asyncio
from typing import Any

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class OpenAICommonFormatter:
    """Converts OpenAI streaming chunks to common format.

    Consumes raw OpenAI SSE chunks and produces chunks in our common
    format that can be processed by policies regardless of backend.
    """

    async def process(
        self,
        input_queue: asyncio.Queue[Any],  # Raw OpenAI chunks
        output_queue: asyncio.Queue[Any],  # Common format chunks
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert OpenAI chunks to common format.

        Reads OpenAI-specific streaming chunks from input_queue, converts
        them to our common format, and writes to output_queue.

        Args:
            input_queue: Queue of raw OpenAI streaming chunks
            output_queue: Queue for common format chunks
            policy_ctx: Policy context (unused in formatter)
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        pass  # TODO: Implement


class AnthropicCommonFormatter:
    """Converts Anthropic streaming chunks to common format.

    Consumes raw Anthropic SSE chunks and produces chunks in our common
    format that can be processed by policies regardless of backend.
    """

    async def process(
        self,
        input_queue: asyncio.Queue[Any],  # Raw Anthropic chunks
        output_queue: asyncio.Queue[Any],  # Common format chunks
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert Anthropic chunks to common format.

        Reads Anthropic-specific streaming chunks from input_queue, converts
        them to our common format, and writes to output_queue.

        Args:
            input_queue: Queue of raw Anthropic streaming chunks
            output_queue: Queue for common format chunks
            policy_ctx: Policy context (unused in formatter)
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        pass  # TODO: Implement


__all__ = ["OpenAICommonFormatter", "AnthropicCommonFormatter"]
