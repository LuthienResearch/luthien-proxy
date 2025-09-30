# ABOUTME: Policy that inserts separator strings between tokens in streaming responses
# ABOUTME: Configurable to insert separators every N tokens with custom separator string

"""Streaming separator policy for inserting delimiters between tokens.

Behavior:
- Pre hook: pass-through
- Post-success: pass-through (non-streaming)
- Streaming: insert separator string every N tokens in delta content
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from .base import LuthienPolicy, StreamPolicyContext


@dataclass
class SeparatorStreamContext(StreamPolicyContext):
    """Context tracking per-stream separator state."""

    every_n: int = 1
    separator_str: str = " | "
    token_count: int = 0


class StreamingSeparatorPolicy(LuthienPolicy):
    """Policy that inserts separator strings between tokens in streaming responses."""

    def __init__(self, options: Optional[dict[str, int | str]] = None):
        """Initialize with configuration options.

        Args:
            options: Configuration dict with:
                - every_n (int): Insert separator every N tokens (default: 1)
                - separator_str (str): String to insert (default: " | ")
        """
        super().__init__()
        opts = options or {}
        self.every_n: int = int(opts.get("every_n", 1))
        self.separator_str: str = str(opts.get("separator_str", " | "))

        if self.every_n < 1:
            raise ValueError("every_n must be at least 1")

    def create_stream_context(self, stream_id: str, request_data: dict) -> SeparatorStreamContext:
        """Build a separator context for the supplied stream."""
        return SeparatorStreamContext(
            stream_id=stream_id,
            original_request=request_data,
            every_n=self.every_n,
            separator_str=self.separator_str,
        )

    async def generate_response_stream(
        self,
        context: SeparatorStreamContext,
        incoming_stream: AsyncIterator[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Insert configured separators while preserving per-stream state."""
        async for chunk in incoming_stream:
            context.chunk_count += 1

            try:
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    content = delta.get("content")

                    if content:
                        context.token_count += 1
                        if context.token_count % context.every_n == 0:
                            delta["content"] = content + context.separator_str

                yield chunk
            except Exception:
                yield chunk
