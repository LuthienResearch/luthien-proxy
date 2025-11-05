# ABOUTME: OpenAI implementation of CommonFormatter
# ABOUTME: Converts OpenAI streaming chunks to common format

"""OpenAI common formatter implementation."""

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
        input_stream: Any,  # OpenAI stream (AsyncIterator)
        output_queue: asyncio.Queue[Any],  # Common format chunks
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert OpenAI chunks to common format.

        Reads OpenAI-specific streaming chunks from input_stream, converts
        them to our common format, and writes to output_queue.

        Args:
            input_stream: Stream of raw OpenAI chunks
            output_queue: Queue for common format chunks
            policy_ctx: Policy context (unused in formatter)
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        pass  # TODO: Implement


__all__ = ["OpenAICommonFormatter"]
