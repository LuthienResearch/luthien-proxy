"""ABOUTME: StreamingChunkAssembler for assembling streaming chunks into blocks.

ABOUTME: Parses chunks, detects block transitions, and calls policy callbacks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from litellm.types.utils import ModelResponse, StreamingChoices

from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.v2.streaming.stream_state import StreamState


class StreamingChunkAssembler:
    """Assembles streaming chunks into blocks with state tracking.

    Responsibilities:
    - Parse streaming chunks and detect block boundaries
    - Aggregate deltas within each block (content → tool calls)
    - Track completion state in StreamState
    - Call policy callback with updated state on each chunk
    - Strip Anthropic's empty content fields during tool call phase

    Usage:
        assembler = StreamingChunkAssembler(on_chunk_callback=policy_handler)
        await assembler.process(incoming_chunks, streaming_context)
    """

    def __init__(
        self,
        on_chunk_callback: Callable[[ModelResponse, StreamState, Any], Awaitable[None]],
    ):
        """Initialize processor with policy callback.

        Args:
            on_chunk_callback: Async function called for each chunk.
                Signature: async def on_chunk(chunk, state, context) -> None
        """
        self.on_chunk = on_chunk_callback
        self.state = StreamState()
        self._tool_call_index_to_id: dict[int, str] = {}
        self._in_tool_call_phase = False

    async def process(
        self,
        incoming: AsyncIterator[ModelResponse],
        context: Any,
    ) -> None:
        """Process streaming chunks until completion.

        Args:
            incoming: Async iterator of model response chunks
            context: Streaming context passed to policy callback
        """
        async for chunk in incoming:
            # Store raw chunk for recording
            self.state.raw_chunks.append(chunk)

            # Update aggregation state and detect transitions
            self._update_state(chunk)

            # Strip empty content from Anthropic tool call chunks
            # This is an Anthropic-specific artifact that confuses policies
            chunk = self._strip_empty_content(chunk)

            # Call policy with updated state
            await self.on_chunk(chunk, self.state, context)

            # Clear just_completed for next chunk
            self.state.just_completed = None

            # Check for stream completion
            if self.state.finish_reason:
                break

    def _update_state(self, chunk: ModelResponse) -> None:
        """Update aggregation state from chunk.

        Detects:
        - Block transitions (content → tool call, tool_call_N → tool_call_N+1)
        - Block completions
        - finish_reason

        Updates:
        - self.state.blocks
        - self.state.current_block
        - self.state.just_completed
        - self.state.finish_reason
        """
        # Extract data from chunk
        if not chunk.choices:
            return

        choice = chunk.choices[0]
        choice = cast(StreamingChoices, choice)
        delta = choice.delta
        finish_reason = choice.finish_reason

        # Extract content from delta (handle both dict and Delta object)
        content = None
        if isinstance(delta, dict):
            content = delta.get("content")
        elif hasattr(delta, "content"):
            content = delta.content  # type: ignore[union-attr]

        # Process content
        # Note: content can be: actual text, empty string "", or null
        # We only process actual text (not null, not empty string)
        if content:  # Truthy check: handles null, empty string, and actual content
            self._process_content_delta(content)

        # Extract tool_calls from delta (handle both dict and Delta object)
        tool_calls = None
        if isinstance(delta, dict):
            tool_calls = delta.get("tool_calls")
        elif hasattr(delta, "tool_calls"):
            tool_calls = delta.tool_calls  # type: ignore[union-attr]

        # Process tool calls
        if tool_calls:
            self._process_tool_call_deltas(tool_calls)

        # Process finish_reason (after processing content/tool_calls in this chunk)
        if finish_reason:
            self.state.finish_reason = finish_reason
            # Mark current block complete if any
            if self.state.current_block and not self.state.current_block.is_complete:
                self.state.current_block.is_complete = True
                self.state.just_completed = self.state.current_block

    def _process_content_delta(self, content: str) -> None:
        """Process a content delta, creating content block if needed."""
        if not self.state.current_block:
            # Start new content block
            block = ContentStreamBlock()
            self.state.blocks.append(block)
            self.state.current_block = block

        if isinstance(self.state.current_block, ContentStreamBlock):
            self.state.current_block.content += content

    def _process_tool_call_deltas(self, tool_calls: Any) -> None:
        """Process tool call deltas, handling block transitions.

        Tool calls stream sequentially by index (0, 1, 2...).
        When index changes, previous tool call is complete.

        Args:
            tool_calls: List of tool call deltas (can be dicts or ChatCompletionDeltaToolCall objects)
        """
        self._in_tool_call_phase = True

        for tc_delta in tool_calls:
            # Extract index (handle both dict and object)
            if isinstance(tc_delta, dict):
                index = tc_delta.get("index")
            else:
                index = tc_delta.index if hasattr(tc_delta, "index") else None

            if index is None:
                continue

            # Check if this is a new tool call (different from current)
            if self.state.current_block:
                if isinstance(self.state.current_block, ToolCallStreamBlock):
                    if self.state.current_block.index != index:
                        # Previous tool call is complete, new one starting
                        self.state.current_block.is_complete = True
                        self.state.just_completed = self.state.current_block
                        self.state.current_block = None
                elif isinstance(self.state.current_block, ContentStreamBlock):
                    # Transition from content to tool calls
                    self.state.current_block.is_complete = True
                    self.state.just_completed = self.state.current_block
                    self.state.current_block = None

            # Get or create tool call block for this index
            # Extract id (handle both dict and object)
            if isinstance(tc_delta, dict):
                tc_id = tc_delta.get("id")
            else:
                tc_id = tc_delta.id if hasattr(tc_delta, "id") else None

            if tc_id:
                # First chunk for this tool call - has id and name
                self._tool_call_index_to_id[index] = tc_id

            # Resolve ID from index
            resolved_id = self._tool_call_index_to_id.get(index)
            if not resolved_id:
                resolved_id = f"tool_{index}"
                self._tool_call_index_to_id[index] = resolved_id

            # Get or create block
            if not self.state.current_block or (
                isinstance(self.state.current_block, ToolCallStreamBlock) and self.state.current_block.index != index
            ):
                # Create new tool call block
                block = ToolCallStreamBlock(id=resolved_id, index=index)
                self.state.blocks.append(block)
                self.state.current_block = block

            # Update tool call data
            if isinstance(self.state.current_block, ToolCallStreamBlock):
                # Extract function delta (handle both dict and object)
                if isinstance(tc_delta, dict):
                    function_delta = tc_delta.get("function", {})
                    name = function_delta.get("name") if isinstance(function_delta, dict) else None
                    arguments = function_delta.get("arguments") if isinstance(function_delta, dict) else None
                else:
                    function_delta = tc_delta.function if hasattr(tc_delta, "function") else None
                    name = function_delta.name if function_delta and hasattr(function_delta, "name") else None
                    arguments = (
                        function_delta.arguments if function_delta and hasattr(function_delta, "arguments") else None
                    )

                if name:
                    self.state.current_block.name = name

                if arguments:
                    self.state.current_block.arguments += arguments

    def _strip_empty_content(self, chunk: ModelResponse) -> ModelResponse:
        """Remove empty content fields from tool call phase chunks.

        Anthropic sends delta.content="" during tool call streaming.
        This is an artifact that confuses policies, so we strip it.

        Returns:
            Modified chunk with empty content removed (if applicable)
        """
        if not self._in_tool_call_phase:
            return chunk

        choices = cast(list[StreamingChoices], chunk.choices)
        if not chunk.choices or not choices[0].delta:
            return chunk

        delta = choices[0].delta
        if not isinstance(delta, dict):
            return chunk

        # Remove empty content field by modifying the delta dict in-place
        if delta.get("content") == "":
            del delta["content"]

        return chunk


__all__ = ["StreamingChunkAssembler"]
