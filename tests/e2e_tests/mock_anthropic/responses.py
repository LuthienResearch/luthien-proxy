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
