# ABOUTME: EventDrivenPolicy implementation of UppercaseNthWord transformation
# ABOUTME: Demonstrates buffering and word-boundary handling with the DSL

"""UppercaseNthWordPolicy using EventDrivenPolicy DSL.

This policy demonstrates text transformation with the EventDrivenPolicy DSL:
- Buffers content to identify complete words
- Tracks word position across chunks
- Uppercases every Nth word before emitting
- Shows how to handle word boundaries in streaming context

This is the event-driven equivalent of UppercaseNthWordPolicy, demonstrating
how the DSL simplifies streaming text transformation.

Example with N=3:
    Input:  "The quick brown fox jumps over the lazy dog"
    Output: "The quick BROWN fox jumps OVER the lazy DOG"
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.streaming import EventDrivenPolicy, StreamingContext

logger = logging.getLogger(__name__)


class EventDrivenUppercaseNthWordPolicy(EventDrivenPolicy, LuthienPolicy):
    """Uppercase every Nth word using EventDrivenPolicy.

    Demonstrates:
    - Per-request state for buffering
    - Content chunk processing
    - Word boundary handling
    - Forwarding transformed chunks
    - Stream finalization

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
        logger.info(f"EventDrivenUppercaseNthWordPolicy initialized with n={n}")

    def create_state(self) -> Any:
        """Create per-request state for word buffering.

        Returns:
            SimpleNamespace with buffer, word_position, chunks_processed
        """
        return SimpleNamespace(
            word_buffer="",  # Buffer for partial word
            word_position=0,  # Current word index (0-based)
            chunks_processed=0,  # Count of chunks seen
        )

    async def on_stream_started(self, state: Any, context: StreamingContext) -> None:
        """Emit event when stream starts."""
        context.emit(
            "event_driven_uppercase.started",
            f"Started streaming transformation (n={self.n})",
            severity="info",
        )

    async def on_content_chunk(
        self, content: str, raw_chunk: ModelResponse, state: Any, context: StreamingContext
    ) -> None:
        """Process content chunk, buffering and uppercasing words.

        Strategy:
        1. Add content to buffer
        2. Extract complete words (delimited by spaces)
        3. Uppercase every Nth word
        4. Emit processed text
        5. Keep incomplete word in buffer

        Args:
            content: Text content from chunk
            raw_chunk: Raw ModelResponse chunk (not forwarded - we send transformed chunks)
            state: Per-request state
            context: Streaming context
        """
        state.chunks_processed += 1

        # Add to buffer
        state.word_buffer += content

        # Process complete words (space indicates word boundary)
        processed_text = ""
        while " " in state.word_buffer:
            # Extract complete word
            space_idx = state.word_buffer.index(" ")
            word = state.word_buffer[:space_idx]
            state.word_buffer = state.word_buffer[space_idx + 1 :]  # Remove word + space

            # Apply uppercase if this is the Nth word
            if (state.word_position % self.n) == (self.n - 1):
                word = word.upper()

            # Accumulate processed text
            processed_text += word + " "
            state.word_position += 1

        # Emit all processed words from this chunk as one batch
        if processed_text:
            transformed_chunk = self._create_text_chunk(processed_text)
            await context.send(transformed_chunk)

    async def on_chunk_complete(self, raw_chunk: ModelResponse, state: Any, context: StreamingContext) -> None:
        """Forward non-content chunks (role, tool calls, finish, etc.).

        We only transform content chunks. Other chunks are passed through.

        Args:
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        # Check if this chunk had content (we already handled it)
        chunk_dict = raw_chunk.model_dump() if hasattr(raw_chunk, "model_dump") else dict(raw_chunk)  # type: ignore
        choices = chunk_dict.get("choices", [])
        if choices and isinstance(choices, list):
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict) and delta.get("content"):
                # Content chunk - already handled in on_content_chunk
                return

        # Non-content chunk - forward as-is
        await context.send(raw_chunk)

    async def on_stream_closed(self, state: Any, context: StreamingContext) -> None:
        """Finalize stream - emit any remaining buffered word.

        Args:
            state: Per-request state
            context: Streaming context
        """
        # Emit any remaining buffered content (last word without trailing space)
        if state.word_buffer:
            # Check if this last word should be uppercase
            if (state.word_position % self.n) == (self.n - 1):
                state.word_buffer = state.word_buffer.upper()

            chunk = self._create_text_chunk(state.word_buffer)
            await context.send(chunk)

        context.emit(
            "event_driven_uppercase.complete",
            "Completed streaming transformation",
            severity="info",
            details={
                "chunks_processed": state.chunks_processed,
                "words_transformed": state.word_position,
                "n": self.n,
            },
        )

    def _create_text_chunk(self, text: str) -> ModelResponse:
        """Create a streaming response chunk with text content.

        Args:
            text: Text to include in chunk

        Returns:
            ModelResponse chunk
        """
        delta = Delta(content=text, role="assistant")
        choice = StreamingChoices(delta=delta, finish_reason=None, index=0)
        return ModelResponse(choices=[choice])

    # ------------------------------------------------------------------
    # LuthienPolicy interface (non-streaming methods)
    # ------------------------------------------------------------------

    async def process_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass request through unchanged - this policy only affects responses."""
        context.emit(
            event_type="event_driven_uppercase.request",
            summary=f"Request passed through (policy only affects responses, n={self.n})",
            severity="info",
        )
        return request

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Transform complete response by uppercasing every Nth word.

        Args:
            response: The ModelResponse to transform
            context: Policy context for event emission

        Returns:
            Transformed ModelResponse
        """
        # Get response as dict and transform
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, message=".*Pydantic serializer warnings.*")
            response_dict = response.model_dump()

        original_content = self._get_content_preview(response_dict)
        transformed_dict = self._transform_response_content(response_dict)
        transformed_content = self._get_content_preview(transformed_dict)

        # Emit event
        context.emit(
            event_type="event_driven_uppercase.applied",
            summary=f"Uppercased every {self.n}th word in response",
            severity="info",
            details={
                "n": self.n,
                "original_preview": original_content[:100] if original_content else "",
                "transformed_preview": transformed_content[:100] if transformed_content else "",
                "word_count": len(original_content.split()) if original_content else 0,
            },
        )

        # Return transformed response
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, message=".*Pydantic serializer warnings.*")
            transformed_response = ModelResponse(**transformed_dict)

        return transformed_response

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
        """Transform response content, uppercasing every Nth word.

        Args:
            response_dict: Response dictionary from LiteLLM

        Returns:
            Transformed response dictionary
        """
        choices = response_dict.get("choices", [])
        if not choices:
            return response_dict

        for choice in choices:
            message = choice.get("message", {})
            if not message:
                continue

            content = message.get("content")
            if content and isinstance(content, str):
                transformed = self._uppercase_nth_word(content)
                message["content"] = transformed

        return response_dict

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


__all__ = ["EventDrivenUppercaseNthWordPolicy"]
