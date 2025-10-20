# ABOUTME: Unit tests for UppercaseNthWordPolicy
# ABOUTME: Tests word transformation, streaming, and edge cases

"""Tests for UppercaseNthWordPolicy."""

import pytest
from litellm import ModelResponse

from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.uppercase_nth_word import UppercaseNthWordPolicy
from luthien_proxy.v2.streaming import ChunkQueue


@pytest.fixture
def policy():
    """Create policy instance with n=3."""
    return UppercaseNthWordPolicy(n=3)


@pytest.fixture
def context():
    """Create a mock policy context."""

    class MockContext:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, summary, severity, details=None):
            self.events.append({"event_type": event_type, "summary": summary, "severity": severity, "details": details})

    return MockContext()


class TestUppercaseNthWord:
    """Test basic word transformation logic."""

    def test_uppercase_nth_word_basic(self, policy):
        """Test basic transformation with n=3."""
        text = "The quick brown fox jumps over the lazy dog"
        result = policy._uppercase_nth_word(text)
        expected = "The quick BROWN fox jumps OVER the lazy DOG"
        assert result == expected

    def test_uppercase_nth_word_exact_multiple(self, policy):
        """Test with exactly n words."""
        text = "one two three"
        result = policy._uppercase_nth_word(text)
        expected = "one two THREE"
        assert result == expected

    def test_uppercase_nth_word_less_than_n(self, policy):
        """Test with fewer than n words."""
        text = "one two"
        result = policy._uppercase_nth_word(text)
        expected = "one two"  # No word gets uppercased
        assert result == expected

    def test_uppercase_nth_word_empty(self, policy):
        """Test with empty string."""
        assert policy._uppercase_nth_word("") == ""

    def test_uppercase_nth_word_single_word(self, policy):
        """Test with single word."""
        text = "hello"
        result = policy._uppercase_nth_word(text)
        assert result == "hello"  # First word is not the 3rd

    def test_uppercase_nth_word_n_equals_1(self):
        """Test with n=1 (uppercase all words)."""
        policy = UppercaseNthWordPolicy(n=1)
        text = "hello world test"
        result = policy._uppercase_nth_word(text)
        expected = "HELLO WORLD TEST"
        assert result == expected

    def test_uppercase_nth_word_n_equals_2(self):
        """Test with n=2 (uppercase every other word)."""
        policy = UppercaseNthWordPolicy(n=2)
        text = "one two three four five"
        result = policy._uppercase_nth_word(text)
        expected = "one TWO three FOUR five"
        assert result == expected

    def test_invalid_n_value(self):
        """Test that n < 1 raises ValueError."""
        with pytest.raises(ValueError, match="n must be >= 1"):
            UppercaseNthWordPolicy(n=0)

        with pytest.raises(ValueError, match="n must be >= 1"):
            UppercaseNthWordPolicy(n=-1)


class TestProcessRequest:
    """Test request processing."""

    @pytest.mark.asyncio
    async def test_process_request_passthrough(self, policy, context):
        """Test that requests pass through unchanged."""
        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )

        result = await policy.process_request(request, context)

        # Request should be unchanged
        assert result == request

        # Should emit event
        assert len(context.events) == 1
        assert context.events[0]["event_type"] == "policy.uppercase_request"
        assert "only affects responses" in context.events[0]["summary"]


class TestProcessFullResponse:
    """Test full (non-streaming) response processing."""

    @pytest.mark.asyncio
    async def test_process_full_response_transforms_content(self, policy, context):
        """Test that response content is transformed."""
        # Create response with test content
        response_dict = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "The quick brown fox jumps over"},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            "model": "gpt-4",
        }
        response = FullResponse.from_model_response(ModelResponse(**response_dict))

        result = await policy.process_full_response(response, context)

        # Extract transformed content
        result_dict = result.response.model_dump()
        transformed_content = result_dict["choices"][0]["message"]["content"]

        # Every 3rd word should be uppercase
        expected = "The quick BROWN fox jumps OVER"
        assert transformed_content == expected

        # Should emit event
        assert len(context.events) == 1
        assert context.events[0]["event_type"] == "policy.uppercase_applied"
        assert "Uppercased every 3th word" in context.events[0]["summary"]

    @pytest.mark.asyncio
    async def test_process_full_response_preserves_other_fields(self, policy, context):
        """Test that non-content fields are preserved."""
        response_dict = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "one two three"},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            "model": "gpt-4",
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }
        response = FullResponse.from_model_response(ModelResponse(**response_dict))

        result = await policy.process_full_response(response, context)

        # Check that metadata is preserved
        result_dict = result.response.model_dump()
        assert result_dict["model"] == "gpt-4"
        assert result_dict["usage"]["total_tokens"] == 13
        assert result_dict["choices"][0]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_process_full_response_empty_content(self, policy, context):
        """Test handling of empty content."""
        response_dict = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            "model": "gpt-4",
        }
        response = FullResponse.from_model_response(ModelResponse(**response_dict))

        result = await policy.process_full_response(response, context)

        # Should handle gracefully
        result_dict = result.response.model_dump()
        assert result_dict["choices"][0]["message"]["content"] == ""

    @pytest.mark.asyncio
    async def test_process_full_response_no_choices(self, policy, context):
        """Test handling of response with no choices."""
        response_dict = {"choices": [], "model": "gpt-4"}
        response = FullResponse.from_model_response(ModelResponse(**response_dict))

        result = await policy.process_full_response(response, context)

        # Should handle gracefully
        result_dict = result.response.model_dump()
        assert result_dict["choices"] == []


class TestProcessStreamingResponse:
    """Test streaming response processing."""

    @pytest.mark.asyncio
    async def test_process_streaming_response_transforms_words(self, policy, context):
        """Test that streaming chunks are transformed correctly."""
        incoming = ChunkQueue[StreamingResponse]()
        outgoing = ChunkQueue[StreamingResponse]()

        # Create chunks with words
        chunks = [
            _create_chunk("The "),
            _create_chunk("quick "),
            _create_chunk("brown "),
            _create_chunk("fox"),
        ]

        # Add chunks to incoming queue
        for chunk in chunks:
            await incoming.put(chunk)
        await incoming.close()

        # Process
        await policy.process_streaming_response(incoming, outgoing, context)

        # Collect output chunks
        output_text = ""
        while True:
            batch = await outgoing.get_available()
            if not batch:
                break
            for chunk in batch:
                text = _extract_text(chunk)
                output_text += text

        # Every 3rd word should be uppercase
        expected = "The quick BROWN fox"
        assert output_text == expected

        # Should emit events
        assert len(context.events) == 2
        assert context.events[0]["event_type"] == "policy.uppercase_streaming_started"
        assert context.events[1]["event_type"] == "policy.uppercase_streaming_complete"

    @pytest.mark.asyncio
    async def test_process_streaming_response_word_boundaries(self, policy, context):
        """Test correct handling of word boundaries across chunks."""
        incoming = ChunkQueue[StreamingResponse]()
        outgoing = ChunkQueue[StreamingResponse]()

        # Split words across chunk boundaries
        chunks = [
            _create_chunk("one "),
            _create_chunk("tw"),
            _create_chunk("o "),
            _create_chunk("three "),
            _create_chunk("four"),
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        await incoming.close()

        await policy.process_streaming_response(incoming, outgoing, context)

        # Collect output
        output_text = ""
        while True:
            batch = await outgoing.get_available()
            if not batch:
                break
            for chunk in batch:
                output_text += _extract_text(chunk)

        # The 3rd word "three" should be uppercase
        expected = "one two THREE four"
        assert output_text == expected

    @pytest.mark.asyncio
    async def test_process_streaming_response_empty_chunks(self, policy, context):
        """Test handling of empty chunks."""
        incoming = ChunkQueue[StreamingResponse]()
        outgoing = ChunkQueue[StreamingResponse]()

        chunks = [
            _create_chunk("one "),
            _create_chunk(""),  # Empty chunk
            _create_chunk("two "),
            _create_chunk("three"),
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        await incoming.close()

        await policy.process_streaming_response(incoming, outgoing, context)

        # Collect output
        output_text = ""
        while True:
            batch = await outgoing.get_available()
            if not batch:
                break
            for chunk in batch:
                output_text += _extract_text(chunk)

        expected = "one two THREE"
        assert output_text == expected


# Helper functions


def _create_chunk(text: str) -> StreamingResponse:
    """Create a streaming response chunk with text."""
    chunk_dict = {
        "choices": [
            {
                "delta": {"content": text, "role": "assistant"},
                "finish_reason": None,
                "index": 0,
            }
        ]
    }
    return StreamingResponse(chunk=ModelResponse(**chunk_dict))


def _extract_text(chunk: StreamingResponse) -> str:
    """Extract text from a chunk."""
    chunk_dict = chunk.chunk.model_dump() if hasattr(chunk.chunk, "model_dump") else chunk.chunk
    choices = chunk_dict.get("choices", [])
    if not choices:
        return ""
    delta = choices[0].get("delta", {})
    content = delta.get("content", "")
    return content if isinstance(content, str) else ""
