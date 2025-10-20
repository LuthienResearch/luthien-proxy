# ABOUTME: Policy that uppercases every Nth word in responses for demonstration
# ABOUTME: Configurable N parameter, only affects message text (not tool calls or other content)

"""UppercaseNthWordPolicy - Uppercases every Nth word in response messages.

This policy demonstrates text transformation with configurable parameters.
It only affects text content in response messages, not tool calls or other
structured content.

Example with N=3:
    Input:  "The quick brown fox jumps over the lazy dog"
    Output: "The quick BROWN fox jumps OVER the lazy DOG"
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.streaming import ChunkQueue

logger = logging.getLogger(__name__)


class UppercaseNthWordPolicy(LuthienPolicy):
    """Uppercase every Nth word in response messages.

    Args:
        n: Uppercase every Nth word (e.g., n=3 means every 3rd word)
    """

    def __init__(self, n: int = 3):
        """Initialize policy with N parameter.

        Args:
            n: Uppercase every Nth word (must be >= 1)
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        self.n = n
        logger.info(f"UppercaseNthWordPolicy initialized with n={n}")

    def _uppercase_nth_word(self, text: str) -> str:
        """Apply uppercase transformation to every Nth word.

        Args:
            text: Input text

        Returns:
            Text with every Nth word uppercased
        """
        if not text:
            return text

        words = text.split()
        for i in range(self.n - 1, len(words), self.n):  # Start at index n-1 (0-indexed)
            words[i] = words[i].upper()

        return " ".join(words)

    def _transform_response_content(self, response_dict: dict[str, Any]) -> dict[str, Any]:
        """Transform response content, uppercasing every Nth word in text.

        Only transforms text content in messages. Preserves tool calls, function calls,
        and other structured content.

        Args:
            response_dict: Response dictionary from LiteLLM

        Returns:
            Transformed response dictionary
        """
        # Get choices (OpenAI format)
        choices = response_dict.get("choices", [])
        if not choices:
            return response_dict

        # Transform each choice
        for choice in choices:
            message = choice.get("message", {})
            if not message:
                continue

            # Only transform text content (not tool calls)
            content = message.get("content")
            if content and isinstance(content, str):
                transformed = self._uppercase_nth_word(content)
                message["content"] = transformed

        return response_dict

    async def process_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass request through unchanged - this policy only affects responses."""
        context.emit(
            event_type="policy.uppercase_request",
            summary=f"Request passed through (policy only affects responses, n={self.n})",
            severity="info",
        )
        return request

    async def process_full_response(self, response: FullResponse, context: PolicyContext) -> FullResponse:
        """Transform complete response by uppercasing every Nth word.

        Args:
            response: The response to transform
            context: Policy context for event emission

        Returns:
            Transformed response
        """
        # Get response as dict and transform
        response_dict = response.response.model_dump()
        original_content = self._get_content_preview(response_dict)

        transformed_dict = self._transform_response_content(response_dict)
        transformed_content = self._get_content_preview(transformed_dict)

        # Emit event describing the transformation
        context.emit(
            event_type="policy.uppercase_applied",
            summary=f"Uppercased every {self.n}th word in response",
            severity="info",
            details={
                "n": self.n,
                "original_preview": original_content[:100] if original_content else "",
                "transformed_preview": transformed_content[:100] if transformed_content else "",
                "word_count": len(original_content.split()) if original_content else 0,
            },
        )

        # Update the response with transformed content
        from litellm import ModelResponse

        transformed_response = ModelResponse(**transformed_dict)
        return FullResponse.from_model_response(transformed_response)

    async def process_streaming_response(
        self,
        incoming: ChunkQueue[StreamingResponse],
        outgoing: ChunkQueue[StreamingResponse],
        context: PolicyContext,
        keepalive: Optional[Callable[[], None]] = None,
    ) -> None:
        """Transform streaming response by uppercasing every Nth word.

        Strategy:
        1. Buffer chunks to collect complete words
        2. Track word position across chunks
        3. Uppercase every Nth word before emitting
        4. Emit transformed chunks
        """
        word_buffer = ""
        word_position = 0  # Track which word we're on (0-indexed)
        chunks_processed = 0

        try:
            context.emit(
                event_type="policy.uppercase_streaming_started",
                summary=f"Started streaming transformation (n={self.n})",
                severity="info",
            )

            while True:
                # Get all available chunks
                batch = await incoming.get_available()
                if not batch:  # Stream ended
                    # Emit any remaining buffered content
                    if word_buffer:
                        # Check if this last word should be uppercase
                        if (word_position % self.n) == (self.n - 1):
                            word_buffer = word_buffer.upper()
                        chunk = self._create_text_chunk(word_buffer)
                        await outgoing.put(chunk)
                    break

                # Process each chunk
                for chunk in batch:
                    chunks_processed += 1

                    # Extract text content from chunk
                    text = self._extract_chunk_text(chunk)
                    if not text:
                        # Pass through non-text chunks unchanged
                        await outgoing.put(chunk)
                        continue

                    # Add to buffer
                    word_buffer += text

                    # Check if we have complete words (space indicates word boundary)
                    while " " in word_buffer:
                        # Extract complete word
                        space_idx = word_buffer.index(" ")
                        word = word_buffer[:space_idx]
                        word_buffer = word_buffer[space_idx + 1 :]  # Keep the space for next iteration

                        # Apply uppercase if this is the Nth word
                        if (word_position % self.n) == (self.n - 1):
                            word = word.upper()

                        # Emit word + space
                        transformed_chunk = self._create_text_chunk(word + " ")
                        await outgoing.put(transformed_chunk)

                        word_position += 1

                    # Keep partial word in buffer for next iteration

            context.emit(
                event_type="policy.uppercase_streaming_complete",
                summary="Completed streaming transformation",
                severity="info",
                details={
                    "chunks_processed": chunks_processed,
                    "words_transformed": word_position,
                    "n": self.n,
                },
            )

        finally:
            # Always close outgoing queue
            await outgoing.close()

    def _extract_chunk_text(self, chunk: StreamingResponse) -> str:
        """Extract text content from a streaming chunk.

        Args:
            chunk: Streaming response chunk

        Returns:
            Text content or empty string
        """
        try:
            chunk_dict = chunk.chunk.model_dump() if hasattr(chunk.chunk, "model_dump") else chunk.chunk
            choices = chunk_dict.get("choices", [])
            if not choices:
                return ""

            delta = choices[0].get("delta", {})
            content = delta.get("content")
            return content if isinstance(content, str) else ""
        except Exception as e:
            logger.warning(f"Failed to extract chunk text: {e}")
            return ""

    def _create_text_chunk(self, text: str) -> StreamingResponse:
        """Create a streaming response chunk with text content.

        Args:
            text: Text to include in chunk

        Returns:
            StreamingResponse chunk
        """
        from litellm import ModelResponse

        chunk_dict = {
            "choices": [
                {
                    "delta": {"content": text, "role": "assistant"},
                    "finish_reason": None,
                    "index": 0,
                }
            ]
        }
        chunk = ModelResponse(**chunk_dict)
        return StreamingResponse(chunk=chunk)

    def _get_content_preview(self, response_dict: dict[str, Any]) -> str:
        """Get a preview of response content for logging.

        Args:
            response_dict: Response dictionary

        Returns:
            Content string or empty string
        """
        choices = response_dict.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        content = message.get("content", "")
        return content if isinstance(content, str) else ""


__all__ = ["UppercaseNthWordPolicy"]
