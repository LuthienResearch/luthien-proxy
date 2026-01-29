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
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("HELLO", "upper"),
            ("hello", "lower"),
            ("Hello", "title"),
            ("hELLO", "mixed"),
            ("", "lower"),
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
            ("cOOl", "radicAL", "rADicAL"),
        ],
    )
    def test_apply_pattern(self, source, replacement, expected):
        assert _apply_capitalization_pattern(source, replacement) == expected


class TestApplyReplacements:
    @pytest.mark.parametrize(
        "text,replacements,match_cap,expected",
        [
            ("hello world", [("hello", "goodbye")], False, "goodbye world"),
            ("hello foo", [("hello", "hi"), ("foo", "bar")], False, "hi bar"),
            ("Hello HELLO hello", [("hello", "hi")], True, "Hi HI hi"),
            ("", [("a", "b")], False, ""),
            ("hello", [], False, "hello"),
            ("[test]", [("[test]", "check")], False, "check"),
        ],
    )
    def test_apply_replacements(self, text, replacements, match_cap, expected):
        assert apply_replacements(text, replacements, match_cap) == expected


class TestNonStreaming:
    @pytest.mark.parametrize(
        "replacements,match_cap,content,expected",
        [
            ([["hello", "goodbye"]], False, "hello world", "goodbye world"),
            ([["hello", "goodbye"]], True, "Hello HELLO hello", "Goodbye GOODBYE goodbye"),
            ([["foo", "bar"]], False, "hello world", "hello world"),
        ],
    )
    async def test_on_response(self, policy_context, replacements, match_cap, content, expected):
        policy = StringReplacementPolicy(replacements=replacements, match_capitalization=match_cap)
        response = ModelResponse(
            id="test-id",
            choices=[Choices(finish_reason="stop", index=0, message=Message(content=content, role="assistant"))],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )
        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == expected


class TestStreaming:
    @pytest.mark.parametrize(
        "content,replacements,expected",
        [
            ("hello world", [["hello", "goodbye"]], "goodbye world"),
            ("say hello world please", [["hello world", "goodbye"]], "say goodbye please"),
        ],
    )
    async def test_on_content_complete(self, streaming_context, content, replacements, expected):
        policy = StringReplacementPolicy(replacements=replacements)

        content_block = ContentStreamBlock(id="content")
        content_block.content = content
        streaming_context.original_streaming_response_state.current_block = content_block

        chunk = make_streaming_chunk(content="", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_content_complete(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk.choices[0].delta.content == expected

    async def test_content_deltas_filtered(self, streaming_context):
        """Content delta chunks are buffered, not passed through."""
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunk = make_streaming_chunk(content="hello foo", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(streaming_context)

        assert streaming_context.egress_queue.empty()

    @pytest.mark.parametrize(
        "content,finish_reason,tool_calls",
        [
            (None, "stop", None),  # Finish reason chunk
            (
                None,
                None,
                [{"index": 0, "id": "call_1", "function": {"name": "f", "arguments": "{}"}, "type": "function"}],
            ),
        ],
    )
    async def test_non_content_chunks_pass_through(self, streaming_context, content, finish_reason, tool_calls):
        """Non-content chunks (finish reason, tool calls) pass through immediately."""
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunk = make_streaming_chunk(
            content=content, model="test-model", id="test-id", finish_reason=finish_reason, tool_calls=tool_calls
        )
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk
