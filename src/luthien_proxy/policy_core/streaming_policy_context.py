"""Streaming policy context for policy hook invocations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.observability.context import ObservabilityContext
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.streaming.stream_state import StreamState


@dataclass
class StreamingPolicyContext:
    """Context for policy invocations during streaming.

    State and functionality that policies can use to implement streaming behavior.
    - Inspect the current stream state (blocks, finish_reason, etc.)
    - Write chunks to egress_queue for client delivery
    - Access shared policy state via policy_ctx
    - Emit observability events
    - Call keepalive() during long-running operations to prevent timeout
    """

    policy_ctx: PolicyContext  # Contains transaction_id, scratchpad, request
    egress_queue: asyncio.Queue[ModelResponse]  # Where policies write chunks
    original_streaming_response_state: StreamState  # Assembler state (auto-updated)
    observability: ObservabilityContext  # For emitting events
    keepalive: Callable[[], None]  # Reset timeout during long-running operations

    def push_chunk(self, chunk: ModelResponse) -> None:
        """Push a chunk to the egress queue."""
        self.egress_queue.put_nowait(chunk)

    @property
    def last_chunk_received(self) -> ModelResponse:
        """Get the most recent chunk received from the LLM."""
        if len(self.original_streaming_response_state.raw_chunks) == 0:
            raise RuntimeError("Can't return last chunk received, no chunks have been received yet")
        return self.original_streaming_response_state.raw_chunks[-1]

    @property
    def transaction_id(self) -> str:
        """Get the transaction ID from the policy context."""
        return self.policy_ctx.transaction_id

    @property
    def request(self):
        """Get the original request from the policy context."""
        return self.policy_ctx.request

    @property
    def scratchpad(self):
        """Get the scratchpad from the policy context."""
        return self.policy_ctx.scratchpad

    @property
    def observability_context(self) -> ObservabilityContext:
        """Get the observability context."""
        return self.observability


__all__ = ["StreamingPolicyContext"]
