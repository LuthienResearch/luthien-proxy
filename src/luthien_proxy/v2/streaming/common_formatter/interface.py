# ABOUTME: CommonFormatter interface - converts backend-specific chunks to common format
# ABOUTME: Implemented by OpenAI and Anthropic formatters

"""Common formatter interface for backend streaming responses."""

import asyncio
from typing import AsyncIterator, Protocol

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class CommonFormatter(Protocol):
    """Converts backend-specific streaming chunks to common format.

    Implementations consume raw backend chunks (OpenAI, Anthropic, etc.)
    and produce ModelResponse chunks in our common format for policy processing.
    """

    async def process(
        self,
        input_stream: AsyncIterator[ModelResponse],
        output_queue: asyncio.Queue[ModelResponse],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert backend chunks to common format.

        Reads backend-specific streaming chunks, converts them to our
        common format, and writes to output_queue.

        Args:
            input_stream: Stream of backend ModelResponse chunks
            output_queue: Queue for common format ModelResponse chunks
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        ...


__all__ = ["CommonFormatter"]
