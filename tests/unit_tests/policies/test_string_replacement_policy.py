"""Unit tests for StringReplacementPolicy."""

import asyncio

import pytest
from litellm.types.utils import Choices, Message, ModelResponse
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk

from luthien_proxy.llm.types import Request
from luthien_proxy.policies.string_replacement_policy import (
    StringReplacementPolicy,
    _apply_capitalization_pattern,
    _detect_capitalization_pattern,
    apply_replacements,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock
from luthien_proxy.streaming.stream_state import StreamState


@pytest.fixture
def policy_context():
    return PolicyContext(
        transaction_id="test-txn-123",
        request=Request(model="test-model", messages=[{"role": "user", "content": "test"}]),
    )


@pytest.fixture
def streaming_context():
    stream_state = StreamState()
    policy_ctx = PolicyContext(
        transaction_id="test-txn-123",
        request=Request(model="test-model", messages=[{"role": "user", "content": "test"}]),
    )
    return StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=asyncio.Queue(),
        original_streaming_response_state=stream_state,
        keepalive=lambda: None,
    )


class TestCapitalizationHelpers:
    """Test capitalization detection and application."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("HELLO", "upper"),
            ("hello", "lower"),
            ("Hello", "title"),
            ("hELLO", "mixed"),
            ("cOOl", "mixed"),
            ("", "lower"),
            ("123", "lower"),
        ],
    )
    def test_detect_pattern(self, text, expected):
        assert _detect_capitalization_pattern(text) == expected

    @pytest.mark.parametrize(
        "source,replacement,expected",
        [
            ("HELLO", "world", "WORLD"),
            ("hello", "WORLD", "world"),
            ("Hello", "world", "World"),
            ("cOOl", "test", "tESt"),
            ("cOOl", "radicAL", "rADicAL"),  # Mixed with longer replacement
            ("hELLo", "ab", "aB"),  # Mixed with shorter replacement
        ],
    )
    def test_apply_pattern(self, source, replacement, expected):
        assert _apply_capitalization_pattern(source, replacement) == expected


class TestApplyReplacements:
    """Test the apply_replacements function."""

    def test_simple_replacement(self):
        assert apply_replacements("hello world", [("hello", "goodbye")], False) == "goodbye world"

    def test_multiple_replacements(self):
        result = apply_replacements("hello foo", [("hello", "hi"), ("foo", "bar")], False)
        assert result == "hi bar"

    def test_case_insensitive_with_preservation(self):
        result = apply_replacements("Hello HELLO hello", [("hello", "hi")], True)
        assert result == "Hi HI hi"

    def test_empty_inputs(self):
        assert apply_replacements("", [("a", "b")], False) == ""
        assert apply_replacements("hello", [], False) == "hello"
        assert apply_replacements("hello", [("", "x")], False) == "hello"

    def test_special_regex_chars(self):
        assert apply_replacements("[test]", [("[test]", "check")], False) == "check"


class TestStringReplacementPolicyNonStreaming:
    """Test non-streaming response handling."""

    async def test_basic_replacement(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["hello", "goodbye"]])
        response = ModelResponse(
            id="test-id",
            choices=[Choices(finish_reason="stop", index=0, message=Message(content="hello world", role="assistant"))],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )
        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "goodbye world"

    async def test_capitalization_preservation(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["hello", "goodbye"]], match_capitalization=True)
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(finish_reason="stop", index=0, message=Message(content="Hello HELLO hello", role="assistant"))
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )
        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "Goodbye GOODBYE goodbye"

    async def test_no_modification_when_no_match(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        response = ModelResponse(
            id="test-id",
            choices=[Choices(finish_reason="stop", index=0, message=Message(content="hello world", role="assistant"))],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )
        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "hello world"


class TestStringReplacementPolicyStreaming:
    """Test streaming response handling."""

    async def test_content_complete_applies_replacement(self, streaming_context):
        policy = StringReplacementPolicy(replacements=[["hello", "goodbye"]])

        content_block = ContentStreamBlock(id="content")
        content_block.content = "hello world"
        streaming_context.original_streaming_response_state.current_block = content_block

        chunk = make_streaming_chunk(content="", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_content_complete(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk.choices[0].delta.content == "goodbye world"

    async def test_cross_chunk_pattern_matching(self, streaming_context):
        """Patterns split across chunks are matched when content block completes."""
        policy = StringReplacementPolicy(replacements=[["hello world", "goodbye"]])

        content_block = ContentStreamBlock(id="content")
        content_block.content = "say hello world please"
        streaming_context.original_streaming_response_state.current_block = content_block

        chunk = make_streaming_chunk(content="", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_content_complete(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk.choices[0].delta.content == "say goodbye please"

    async def test_content_deltas_filtered(self, streaming_context):
        """Content delta chunks are not passed through (buffered for on_content_complete)."""
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunk = make_streaming_chunk(content="hello foo", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(streaming_context)

        assert streaming_context.egress_queue.empty()

    async def test_tool_calls_pass_through(self, streaming_context):
        """Tool call chunks pass through immediately."""
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunk = make_streaming_chunk(
            content=None,
            model="test-model",
            id="test-id",
            finish_reason=None,
            tool_calls=[
                {"index": 0, "id": "call_123", "function": {"name": "get_foo", "arguments": "{}"}, "type": "function"}
            ],
        )
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk

    async def test_finish_reason_passes_through(self, streaming_context):
        """Finish reason chunks pass through immediately."""
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunk = make_streaming_chunk(content=None, model="test-model", id="test-id", finish_reason="stop")
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk
