# ABOUTME: ClientFormatter interface - converts common format to client-specific SSE
# ABOUTME: Implemented by OpenAI and Anthropic formatters

"""Client formatter interface for streaming responses."""

import asyncio
from typing import Protocol

from litellm.types.utils import ModelResponse

from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.policy_core.policy_context import PolicyContext


class ClientFormatter(Protocol):
    """Converts common format chunks to client-specific SSE strings.

    Implementations consume ModelResponse chunks (from policy egress)
    and produce client-specific SSE formatted strings (OpenAI or Anthropic).
    """

    async def process(
        self,
        input_queue: asyncio.Queue[ModelResponse | None],
        output_queue: asyncio.Queue[str | None],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Convert common format chunks to client SSE format.

        Reads ModelResponse chunks from input queue, converts them to
        client-specific SSE strings, and writes to output queue.

        Args:
            input_queue: Queue to read ModelResponse chunks from
            output_queue: Queue to write SSE formatted strings to
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On conversion errors or malformed chunks
        """
        ...


__all__ = ["ClientFormatter"]
