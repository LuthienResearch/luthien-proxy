"""Mock response types and builders for the Anthropic API mock server."""

from dataclasses import dataclass, field


@dataclass
class MockResponse:
    """A configurable mock response returned by the mock Anthropic server.

    Attributes:
        text: The text content to return in the response.
        model: The model name to report in the response.
        stop_reason: The stop reason to report ("end_turn", "max_tokens", etc.).
        input_tokens: Fake input token count to report in usage.
        output_tokens: Fake output token count to report in usage.
        stream_chunks: If set, splits text into these chunk sizes for SSE streaming.
            Defaults to splitting by word.
    """

    text: str = "mock response"
    model: str = "claude-haiku-4-5"
    stop_reason: str = "end_turn"
    input_tokens: int = 10
    output_tokens: int = 5
    stream_chunks: list[str] = field(default_factory=list)

    def get_chunks(self) -> list[str]:
        """Return the text split into streaming chunks."""
        if self.stream_chunks:
            return self.stream_chunks
        # Default: split by word, keeping trailing space
        words = self.text.split(" ")
        return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]


@dataclass
class MockErrorResponse:
    """A configurable mock error response returned by the mock Anthropic server.

    Attributes:
        status_code: The HTTP status code to return.
        error_type: The Anthropic error type string (e.g. "internal_server_error").
        error_message: The human-readable error message.
    """

    status_code: int = 500
    error_type: str = "internal_server_error"
    error_message: str = "Internal server error"


@dataclass
class MockToolResponse:
    """A configurable mock tool_use response returned by the mock Anthropic server.

    Attributes:
        tool_name: The name of the tool being called.
        tool_input: The input dict to pass to the tool.
        tool_id: The tool call ID. Auto-generated as "toolu_<uuid>" if empty.
        model: The model name to report in the response.
        stop_reason: The stop reason (always "tool_use" for tool calls).
        input_tokens: Fake input token count to report in usage.
        output_tokens: Fake output token count to report in usage.
    """

    tool_name: str
    tool_input: dict
    tool_id: str = ""
    model: str = "claude-haiku-4-5"
    stop_reason: str = "tool_use"
    input_tokens: int = 10
    output_tokens: int = 20


def text_response(text: str, **kwargs) -> MockResponse:
    """Create a simple non-streaming mock response."""
    return MockResponse(text=text, **kwargs)


def stream_response(text: str, chunks: list[str] | None = None, **kwargs) -> MockResponse:
    """Create a streaming mock response.

    Args:
        text: Full text of the response (used to compute output_tokens if not set).
        chunks: Optional explicit list of SSE text chunks. Defaults to splitting by word.
        **kwargs: Additional MockResponse fields.
    """
    if chunks is not None:
        kwargs["stream_chunks"] = chunks
    return MockResponse(text=text, **kwargs)


def error_response(
    status_code: int = 500,
    error_type: str = "internal_server_error",
    error_message: str = "Internal server error",
) -> MockErrorResponse:
    """Create a mock error response.

    Args:
        status_code: The HTTP status code to return.
        error_type: The Anthropic error type string.
        error_message: The human-readable error message.
    """
    return MockErrorResponse(
        status_code=status_code,
        error_type=error_type,
        error_message=error_message,
    )


def tool_response(tool_name: str, tool_input: dict, **kwargs) -> MockToolResponse:
    """Create a mock tool_use response.

    Args:
        tool_name: The name of the tool being called.
        tool_input: The input dict to pass to the tool.
        **kwargs: Additional MockToolResponse fields.
    """
    return MockToolResponse(tool_name=tool_name, tool_input=tool_input, **kwargs)
