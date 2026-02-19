"""Stream block types for aggregating streaming response chunks."""

from __future__ import annotations

from dataclasses import dataclass

from litellm.types.utils import ChatCompletionMessageToolCall, Function


@dataclass
class StreamBlock:
    """Base class for streaming response blocks.

    Blocks stream sequentially: [content?] → [tool_call_0?] → [tool_call_1?] → ...
    Each block accumulates data from multiple chunks until complete.
    """

    id: str
    is_complete: bool = False


@dataclass
class ContentStreamBlock(StreamBlock):
    """Content/message block in a streaming response.

    Accumulates text content from delta.content fields.
    There is at most one content block per response, always first if present.
    """

    content: str = ""

    def __init__(self, id: str = "content"):
        """Initialize content block.

        Args:
            id: Block identifier, defaults to "content" (only one per response)
        """
        super().__init__(id=id, is_complete=False)
        self.content = ""


@dataclass
class ToolCallStreamBlock(StreamBlock):
    """Tool call block in a streaming response.

    Accumulates tool call data from delta.tool_calls fields.
    Each tool call streams sequentially with a unique index (0, 1, 2...).

    The arguments field contains raw JSON string, NOT parsed dict.
    Policies should parse when needed and handle JSONDecodeError.
    """

    name: str = ""
    arguments: str = ""
    index: int = 0

    def __init__(self, id: str, index: int, name: str = "", arguments: str = ""):
        """Initialize tool call block.

        Args:
            id: Tool call identifier (from delta.tool_calls[].id)
            index: Sequential index from stream (0, 1, 2...)
            name: Function name (set in first chunk)
            arguments: Raw JSON string (accumulated across chunks)
        """
        super().__init__(id=id, is_complete=False)
        self.name = name
        self.arguments = arguments
        self.index = index

    @property
    def tool_call(self) -> ChatCompletionMessageToolCall:
        """Get tool call as ChatCompletionMessageToolCall object."""
        return ChatCompletionMessageToolCall(
            id=self.id,
            function=Function(name=self.name, arguments=self.arguments),
        )


__all__ = [
    "StreamBlock",
    "ContentStreamBlock",
    "ToolCallStreamBlock",
]
