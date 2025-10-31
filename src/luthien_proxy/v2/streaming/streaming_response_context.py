# ABOUTME: StreamingResponseContext provides context for policy invocations during streaming
# ABOUTME: Includes transaction_id, request, assembler state, egress queue, and observability

"""Module docstring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.observability.context import ObservabilityContext
    from luthien_proxy.v2.streaming.stream_state import StreamState
    from luthien_proxy.v2.streaming.streaming_chunk_assembler import (
        StreamingChunkAssembler,
    )


@dataclass
class StreamingResponseContext:
    """Context for policy invocations during streaming."""

    transaction_id: str
    final_request: Request
    ingress_assembler: StreamingChunkAssembler | None
    egress_queue: asyncio.Queue[ModelResponse]
    scratchpad: dict[str, Any]
    observability: ObservabilityContext

    @property
    def ingress_state(self) -> StreamState:
        """Current ingress state.

        Raises:
            RuntimeError: If ingress_assembler not yet initialized
        """
        if self.ingress_assembler is None:
            raise RuntimeError("ingress_assembler not yet initialized")
        return self.ingress_assembler.state


__all__ = ["StreamingResponseContext"]
