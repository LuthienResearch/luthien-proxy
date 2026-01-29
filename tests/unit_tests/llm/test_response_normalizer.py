"""Unit tests for the response_normalizer module.

Tests normalize_chunk(), normalize_chunk_with_finish_reason(), and normalize_stream()
to ensure litellm >= 1.81.0 compatibility (dict delta → Delta conversion, finish_reason preservation).
"""

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.llm.response_normalizer import (
    normalize_chunk,
    normalize_chunk_with_finish_reason,
    normalize_response,
    normalize_stream,
)


class TestNormalizeChunk:
    """Tests for normalize_chunk() function."""

    def test_converts_dict_delta_to_delta_object(self):
        """Dict deltas are converted to Delta objects for consistent attribute access."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta={"role": "assistant", "content": "hello"},  # dict, not Delta
                    finish_reason=None,
                )
            ],
        )

        result = normalize_chunk(chunk)

        delta = result.choices[0].delta
        assert isinstance(delta, Delta)
        assert delta.role == "assistant"
        assert delta.content == "hello"

    def test_preserves_already_normalized_delta(self):
        """Delta objects maintain their values (litellm may recreate the object)."""
        original_delta = Delta(role="assistant", content="world")
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=original_delta,
                    finish_reason=None,
                )
            ],
        )

        result = normalize_chunk(chunk)

        # Note: litellm/pydantic may recreate the Delta object, so we check equality not identity
        delta = result.choices[0].delta
        assert isinstance(delta, Delta)
        assert delta.role == "assistant"
        assert delta.content == "world"

    def test_handles_empty_choices(self):
        """Chunks with no choices are returned unchanged."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[],
        )

        result = normalize_chunk(chunk)

        assert result is chunk
        assert result.choices == []

    def test_handles_none_choices(self):
        """Chunks with None choices attribute are handled gracefully."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
        )
        # Ensure choices is falsy for the guard check
        chunk.choices = None  # type: ignore[assignment]

        result = normalize_chunk(chunk)

        assert result is chunk

    def test_normalizes_multiple_choices(self):
        """All choices in a chunk are normalized, not just the first."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta={"content": "first"},
                    finish_reason=None,
                ),
                StreamingChoices(
                    index=1,
                    delta={"content": "second"},
                    finish_reason=None,
                ),
            ],
        )

        result = normalize_chunk(chunk)

        assert isinstance(result.choices[0].delta, Delta)
        assert isinstance(result.choices[1].delta, Delta)
        assert result.choices[0].delta.content == "first"
        assert result.choices[1].delta.content == "second"

    def test_preserves_tool_calls_in_dict_delta(self):
        """Tool calls in dict deltas are preserved after conversion."""
        tool_calls = [{"index": 0, "id": "call_123", "function": {"name": "test_fn", "arguments": "{}"}}]
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta={"role": "assistant", "tool_calls": tool_calls},
                    finish_reason=None,
                )
            ],
        )

        result = normalize_chunk(chunk)

        delta = result.choices[0].delta
        assert isinstance(delta, Delta)
        # Tool calls are converted to ChatCompletionDeltaToolCall objects by pydantic
        assert delta.tool_calls is not None
        assert len(delta.tool_calls) == 1
        assert delta.tool_calls[0].id == "call_123"
        assert delta.tool_calls[0].function.name == "test_fn"
        assert delta.tool_calls[0].function.arguments == "{}"


class TestNormalizeChunkWithFinishReason:
    """Tests for normalize_chunk_with_finish_reason() function."""

    def test_restores_none_finish_reason(self):
        """Restores None finish_reason that litellm defaults to 'stop'."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content="hello"),
                    finish_reason="stop",  # litellm's default, but we want None
                )
            ],
        )

        result = normalize_chunk_with_finish_reason(chunk, intended_finish_reason=None)

        assert result.choices[0].finish_reason is None

    def test_preserves_intended_finish_reason(self):
        """Preserves explicit finish_reason values."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content=""),
                    finish_reason=None,
                )
            ],
        )

        result = normalize_chunk_with_finish_reason(chunk, intended_finish_reason="stop")

        assert result.choices[0].finish_reason == "stop"

    def test_also_normalizes_dict_delta(self):
        """Also converts dict deltas to Delta objects."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta={"content": "test"},
                    finish_reason="stop",
                )
            ],
        )

        result = normalize_chunk_with_finish_reason(chunk, intended_finish_reason=None)

        assert isinstance(result.choices[0].delta, Delta)
        assert result.choices[0].finish_reason is None

    def test_handles_empty_choices(self):
        """Empty choices list is handled gracefully."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[],
        )

        result = normalize_chunk_with_finish_reason(chunk, intended_finish_reason="stop")

        assert result.choices == []

    def test_sets_finish_reason_on_all_choices(self):
        """finish_reason is set on all choices, not just the first."""
        chunk = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(index=0, delta=Delta(content="a"), finish_reason="stop"),
                StreamingChoices(index=1, delta=Delta(content="b"), finish_reason="stop"),
            ],
        )

        result = normalize_chunk_with_finish_reason(chunk, intended_finish_reason=None)

        assert result.choices[0].finish_reason is None
        assert result.choices[1].finish_reason is None


class TestNormalizeStream:
    """Tests for normalize_stream() async generator."""

    @pytest.mark.asyncio
    async def test_normalizes_each_chunk_in_stream(self):
        """Each chunk in the stream is normalized."""

        async def mock_stream():
            yield ModelResponse(
                id="chunk-1",
                model="gpt-4",
                object="chat.completion.chunk",
                choices=[StreamingChoices(index=0, delta={"content": "Hello"}, finish_reason=None)],
            )
            yield ModelResponse(
                id="chunk-2",
                model="gpt-4",
                object="chat.completion.chunk",
                choices=[StreamingChoices(index=0, delta={"content": " world"}, finish_reason=None)],
            )

        chunks = []
        async for chunk in normalize_stream(mock_stream()):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert all(isinstance(c.choices[0].delta, Delta) for c in chunks)
        assert chunks[0].choices[0].delta.content == "Hello"
        assert chunks[1].choices[0].delta.content == " world"

    @pytest.mark.asyncio
    async def test_handles_empty_stream(self):
        """Empty streams yield no chunks."""

        async def empty_stream():
            return
            yield  # Make this a generator

        chunks = [c async for c in normalize_stream(empty_stream())]

        assert chunks == []

    @pytest.mark.asyncio
    async def test_preserves_already_normalized_chunks(self):
        """Already-normalized chunks pass through unchanged."""

        async def mock_stream():
            yield ModelResponse(
                id="chunk-1",
                model="gpt-4",
                object="chat.completion.chunk",
                choices=[StreamingChoices(index=0, delta=Delta(content="pre-normalized"), finish_reason=None)],
            )

        chunks = [c async for c in normalize_stream(mock_stream())]

        assert len(chunks) == 1
        assert isinstance(chunks[0].choices[0].delta, Delta)
        assert chunks[0].choices[0].delta.content == "pre-normalized"


class TestNormalizeResponse:
    """Tests for normalize_response() function."""

    def test_passthrough_returns_same_response(self):
        """Non-streaming responses are returned unchanged (passthrough)."""
        from litellm.types.utils import Choices, Message

        response = ModelResponse(
            id="test-id",
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Hello"),
                    finish_reason="stop",
                )
            ],
        )

        result = normalize_response(response)

        assert result is response
