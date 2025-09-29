# ABOUTME: Policy that inserts separator strings between tokens in streaming responses
# ABOUTME: Configurable to insert separators every N tokens with custom separator string

"""Streaming separator policy for inserting delimiters between tokens.

Behavior:
- Pre hook: pass-through
- Post-success: pass-through (non-streaming)
- Streaming: insert separator string every N tokens in delta content
"""

from __future__ import annotations

from typing import Any, Optional

from .base import LuthienPolicy


class StreamingSeparatorPolicy(LuthienPolicy):
    """Policy that inserts separator strings between tokens in streaming responses."""

    def __init__(self, options: Optional[dict[str, Any]] = None):
        """Initialize with configuration options.

        Args:
            options: Configuration dict with:
                - every_n (int): Insert separator every N tokens (default: 1)
                - separator_str (str): String to insert (default: " | ")
        """
        super().__init__()
        opts = options or {}
        self.every_n: int = opts.get("every_n", 1)
        self.separator_str: str = opts.get("separator_str", " | ")
        self.token_count: int = 0

        if self.every_n < 1:
            raise ValueError("every_n must be at least 1")

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Optional[dict[str, Any]],
        response: Any,
        request_data: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Insert separator string every N tokens in streaming delta content."""
        try:
            response = dict(response)
            for c in response.get("choices", []):
                delta = c.get("delta", {})
                content = delta.get("content")

                if content:
                    # Increment token count and insert separator if needed
                    self.token_count += 1
                    if self.token_count % self.every_n == 0:
                        delta["content"] = content + self.separator_str

            return response
        except Exception:
            # On any failure, return original response
            return response
