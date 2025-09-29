"""Abstract base for Luthien Control Policies with streaming support."""

from __future__ import annotations

import time
from abc import ABC
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from litellm.integrations.custom_logger import CustomLogger


@dataclass
class StreamPolicyContext:
    """Base state object for streaming policies.

    Attributes:
        stream_id: Identifier provided by LiteLLM for this stream.
        original_request: Request payload associated with the stream.
        chunk_count: Number of chunks processed so far.
        start_time: Timestamp captured when the stream context is created.
    """

    stream_id: str
    original_request: dict[str, object]
    chunk_count: int = 0
    start_time: float = field(default_factory=time.time)


class LuthienPolicy(ABC, CustomLogger):
    """Mirror of LiteLLM hook API, executed server-side in the control plane."""

    def __init__(self) -> None:
        """Initialise policy base class and underlying CustomLogger."""
        super().__init__()

    # ------------------------------------------------------------------
    # Streaming API
    # ------------------------------------------------------------------
    def create_stream_context(self, stream_id: str, request_data: dict) -> StreamPolicyContext:
        """Create per-stream state when a stream starts."""
        return StreamPolicyContext(stream_id=stream_id, original_request=request_data)

    async def generate_response_stream(
        self,
        context: StreamPolicyContext,
        incoming_stream: AsyncIterator[dict],
    ) -> AsyncIterator[dict]:
        """Default implementation that forwards chunks unchanged."""
        async for chunk in incoming_stream:
            context.chunk_count += 1
            yield chunk

    # ------------------------------------------------------------------
    # Legacy HTTP hook compatibility
    # ------------------------------------------------------------------
    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Optional[dict],
        response: dict,
        request_data: dict,
    ) -> Optional[dict]:
        """Fallback path used by the legacy HTTP hook endpoint."""
        stream_id = str(request_data.get("litellm_call_id") or "fallback-stream")
        context = self.create_stream_context(stream_id, request_data)

        async def single_chunk_stream() -> AsyncIterator[dict]:
            yield response

        async for transformed in self.generate_response_stream(context, single_chunk_stream()):
            return transformed

        return response


__all__ = [
    "LuthienPolicy",
    "StreamPolicyContext",
]
