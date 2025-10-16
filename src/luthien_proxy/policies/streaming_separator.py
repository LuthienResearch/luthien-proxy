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
from typing import Any, AsyncIterator, Mapping

from luthien_proxy.types import JSONObject, JSONValue

from .base import LuthienPolicy, StreamPolicyContext


@dataclass
class SeparatorStreamContext(StreamPolicyContext):
    """Context tracking per-stream separator state."""

    every_n: int = 1
    separator_str: str = " | "
    token_count: int = 0


class StreamingSeparatorPolicy(LuthienPolicy):
    """Policy that inserts separator strings between tokens in streaming responses."""

    def __init__(self, options: Mapping[str, JSONValue] | None = None):
        """Initialize with configuration options.

        Args:
            options: Configuration dict with:
                - every_n (int): Insert separator every N tokens (default: 1)
                - separator_str (str): String to insert (default: " | ")
        """
        super().__init__(options=options)
        resolved_options: JSONObject = dict(options) if options is not None else {}
        self.every_n = self._parse_every_n(resolved_options.get("every_n"))
        self.separator_str = self._parse_separator(resolved_options.get("separator_str"))

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

    @staticmethod
    def _parse_every_n(raw_value: JSONValue | None) -> int:
        if raw_value is None:
            return 1
        if isinstance(raw_value, bool):
            raise TypeError("every_n must be an integer greater than 0")
        if isinstance(raw_value, (int, float, str)):
            try:
                return int(raw_value)
            except ValueError as exc:
                raise ValueError("every_n must be an integer-compatible value") from exc
        raise TypeError("every_n must be an integer-compatible value")

    @staticmethod
    def _parse_separator(raw_value: JSONValue | None) -> str:
        if raw_value is None:
            return " | "
        if isinstance(raw_value, str):
            return raw_value
        raise TypeError("separator_str must be a string")
