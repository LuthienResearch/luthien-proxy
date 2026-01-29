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
from luthien_proxy.streaming.stream_state import StreamState


@pytest.fixture
def policy_context():
    """Create a basic policy context."""
    return PolicyContext(
        transaction_id="test-txn-123",
        request=Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        ),
    )


@pytest.fixture
def streaming_context():
    """Create a streaming policy context."""
    stream_state = StreamState()
    policy_ctx = PolicyContext(
        transaction_id="test-txn-123",
        request=Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        ),
    )
    egress_queue = asyncio.Queue()
    return StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=egress_queue,
        original_streaming_response_state=stream_state,
        keepalive=lambda: None,
    )


class TestDetectCapitalizationPattern:
    """Test capitalization pattern detection."""

    def test_all_uppercase(self):
        assert _detect_capitalization_pattern("HELLO") == "upper"
        assert _detect_capitalization_pattern("ABC") == "upper"
        assert _detect_capitalization_pattern("HELLO123") == "upper"

    def test_all_lowercase(self):
        assert _detect_capitalization_pattern("hello") == "lower"
        assert _detect_capitalization_pattern("abc") == "lower"
        assert _detect_capitalization_pattern("hello123") == "lower"

    def test_title_case(self):
        assert _detect_capitalization_pattern("Hello") == "title"
        assert _detect_capitalization_pattern("Abc") == "title"
        assert _detect_capitalization_pattern("Hello123world") == "title"

    def test_mixed_case(self):
        assert _detect_capitalization_pattern("hELLO") == "mixed"
        assert _detect_capitalization_pattern("HeLLo") == "mixed"
        assert _detect_capitalization_pattern("cOOl") == "mixed"
        assert _detect_capitalization_pattern("AbC") == "mixed"

    def test_empty_string(self):
        assert _detect_capitalization_pattern("") == "lower"

    def test_no_alpha_chars(self):
        assert _detect_capitalization_pattern("123") == "lower"
        assert _detect_capitalization_pattern("!@#") == "lower"


class TestApplyCapitalizationPattern:
    """Test capitalization pattern application."""

    def test_apply_uppercase(self):
        assert _apply_capitalization_pattern("HELLO", "world") == "WORLD"
        assert _apply_capitalization_pattern("ABC", "xyz") == "XYZ"

    def test_apply_lowercase(self):
        assert _apply_capitalization_pattern("hello", "WORLD") == "world"
        assert _apply_capitalization_pattern("abc", "XYZ") == "xyz"

    def test_apply_title_case(self):
        assert _apply_capitalization_pattern("Hello", "world") == "World"
        assert _apply_capitalization_pattern("Abc", "xyz") == "Xyz"

    def test_apply_mixed_same_length(self):
        # Source: cOOl (c=lower, O=upper, O=upper, l=lower)
        # Target: test -> tESt (t=lower, E=upper, S=upper, t=lower)
        assert _apply_capitalization_pattern("cOOl", "test") == "tESt"

    def test_apply_mixed_replacement_longer(self):
        # Source: cOOl (c=lower, O=upper, O=upper, l=lower)
        # Target: radicAL -> rADical (r=lower, A=upper, D=upper, i=lower, cAL literal)
        result = _apply_capitalization_pattern("cOOl", "radicAL")
        assert result == "rADicAL"

    def test_apply_mixed_replacement_shorter(self):
        # Source: hELLo (h=lower, E=upper, L=upper, L=upper, o=lower)
        # Target: ab -> aB (a=lower, B=upper)
        result = _apply_capitalization_pattern("hELLo", "ab")
        assert result == "aB"

    def test_apply_mixed_with_non_alpha(self):
        # Non-alpha chars are preserved
        assert _apply_capitalization_pattern("HeLLo", "a-b-c") == "A-b-C"


class TestApplyReplacements:
    """Test the apply_replacements function."""

    def test_simple_replacement(self):
        result = apply_replacements("hello world", [("hello", "goodbye")], False)
        assert result == "goodbye world"

    def test_multiple_replacements(self):
        result = apply_replacements(
            "hello world foo",
            [("hello", "goodbye"), ("foo", "bar")],
            False,
        )
        assert result == "goodbye world bar"

    def test_case_sensitive(self):
        result = apply_replacements("Hello HELLO hello", [("hello", "hi")], False)
        assert result == "Hello HELLO hi"

    def test_case_insensitive_match(self):
        result = apply_replacements("Hello HELLO hello", [("hello", "hi")], True)
        assert result == "Hi HI hi"

    def test_empty_text(self):
        result = apply_replacements("", [("hello", "hi")], False)
        assert result == ""

    def test_empty_replacements(self):
        result = apply_replacements("hello world", [], False)
        assert result == "hello world"

    def test_no_match(self):
        result = apply_replacements("hello world", [("foo", "bar")], False)
        assert result == "hello world"

    def test_empty_from_string_skipped(self):
        result = apply_replacements("hello", [("", "bar")], False)
        assert result == "hello"

    def test_multiple_occurrences(self):
        result = apply_replacements("foo foo foo", [("foo", "bar")], False)
        assert result == "bar bar bar"


class TestStringReplacementPolicyInit:
    """Test policy initialization."""

    def test_default_config(self):
        policy = StringReplacementPolicy()
        assert policy._replacements == []
        assert policy._match_capitalization is False

    def test_with_replacements(self):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"], ["hello", "goodbye"]])
        assert policy._replacements == [("foo", "bar"), ("hello", "goodbye")]

    def test_with_match_capitalization(self):
        policy = StringReplacementPolicy(match_capitalization=True)
        assert policy._match_capitalization is True

    def test_get_config(self):
        policy = StringReplacementPolicy(
            replacements=[["foo", "bar"]],
            match_capitalization=True,
        )
        config = policy.get_config()
        assert config == {
            "replacements": [["foo", "bar"]],
            "match_capitalization": True,
        }

    def test_policy_name(self):
        policy = StringReplacementPolicy()
        assert policy.short_policy_name == "StringReplacementPolicy"


class TestStringReplacementPolicyNonStreaming:
    """Test non-streaming response handling."""

    async def test_basic_replacement(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["hello", "goodbye"]])
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="hello world", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "goodbye world"

    async def test_case_insensitive_replacement(self, policy_context):
        policy = StringReplacementPolicy(
            replacements=[["hello", "goodbye"]],
            match_capitalization=True,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="Hello HELLO hello", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "Goodbye GOODBYE goodbye"

    async def test_multiple_choices(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="foo one", role="assistant"),
                ),
                Choices(
                    finish_reason="stop",
                    index=1,
                    message=Message(content="foo two", role="assistant"),
                ),
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "bar one"
        assert result.choices[1].message.content == "bar two"

    async def test_no_replacements_configured(self, policy_context):
        policy = StringReplacementPolicy()
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="hello world", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "hello world"

    async def test_empty_choices(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        response = ModelResponse(
            id="test-id",
            choices=[],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert len(result.choices) == 0

    async def test_none_content(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content=None, role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content is None


class TestStringReplacementPolicyStreaming:
    """Test streaming response handling."""

    async def test_basic_replacement(self, streaming_context):
        policy = StringReplacementPolicy(replacements=[["hello", "goodbye"]])
        chunk = make_streaming_chunk(
            content="hello world",
            model="test-model",
            id="test-id",
            finish_reason=None,
        )

        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
        await policy.on_content_delta(streaming_context)

        assert chunk.choices[0].delta.content == "goodbye world"
        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk

    async def test_case_insensitive_replacement(self, streaming_context):
        policy = StringReplacementPolicy(
            replacements=[["hello", "goodbye"]],
            match_capitalization=True,
        )
        chunk = make_streaming_chunk(
            content="HELLO there",
            model="test-model",
            id="test-id",
            finish_reason=None,
        )

        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
        await policy.on_content_delta(streaming_context)

        assert chunk.choices[0].delta.content == "GOODBYE there"

    async def test_empty_content_delta(self, streaming_context):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunk = make_streaming_chunk(
            content=None,
            model="test-model",
            id="test-id",
            finish_reason=None,
        )

        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
        await policy.on_content_delta(streaming_context)

        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk

    async def test_tool_call_delta_unchanged(self, streaming_context):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunk = make_streaming_chunk(
            content=None,
            model="test-model",
            id="test-id",
            finish_reason=None,
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_123",
                    "function": {"name": "get_foo", "arguments": '{"foo": "'},
                    "type": "function",
                }
            ],
        )

        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
        await policy.on_tool_call_delta(streaming_context)

        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk
        # Tool call should not be modified
        assert chunk.choices[0].delta.tool_calls[0]["function"]["name"] == "get_foo"

    async def test_multiple_content_deltas(self, streaming_context):
        policy = StringReplacementPolicy(replacements=[["foo", "bar"]])
        chunks_and_expected = [
            ("Hello foo ", "Hello bar "),
            ("foo world", "bar world"),
            ("!", "!"),
        ]

        for original, expected in chunks_and_expected:
            chunk = make_streaming_chunk(
                content=original,
                model="test-model",
                id="test-id",
                finish_reason=None,
            )

            streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
            await policy.on_content_delta(streaming_context)

            assert chunk.choices[0].delta.content == expected


class TestCapitalizationPreservation:
    """Test various capitalization preservation scenarios."""

    async def test_user_example_cool_to_radical(self, policy_context):
        """Test the specific example from the requirements: 'cOOl' -> 'rADicAL'."""
        policy = StringReplacementPolicy(
            replacements=[["cool", "radicAL"]],
            match_capitalization=True,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="That is cOOl!", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        # c=lower->r lower, O=upper->A upper, O=upper->D upper, l=lower->i lower, cAL literal
        assert result.choices[0].message.content == "That is rADicAL!"

    async def test_all_caps_preservation(self, policy_context):
        policy = StringReplacementPolicy(
            replacements=[["hello", "goodbye"]],
            match_capitalization=True,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="HELLO WORLD", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "GOODBYE WORLD"

    async def test_all_lower_preservation(self, policy_context):
        policy = StringReplacementPolicy(
            replacements=[["HELLO", "GOODBYE"]],
            match_capitalization=True,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="hello world", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "goodbye world"

    async def test_title_case_preservation(self, policy_context):
        policy = StringReplacementPolicy(
            replacements=[["hello", "goodbye"]],
            match_capitalization=True,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="Hello World", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "Goodbye World"

    async def test_mixed_occurrences(self, policy_context):
        """Test that each occurrence preserves its own capitalization."""
        policy = StringReplacementPolicy(
            replacements=[["test", "check"]],
            match_capitalization=True,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="test TEST Test tEsT", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "check CHECK Check cHeCk"


class TestStringReplacementPolicyRequest:
    """Test request handling (should pass through unchanged)."""

    async def test_request_unchanged(self, policy_context):
        policy = StringReplacementPolicy(replacements=[["hello", "goodbye"]])
        request = Request(
            model="test-model",
            messages=[
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "hello back"},
            ],
            max_tokens=100,
            temperature=0.7,
        )

        result = await policy.on_request(request, policy_context)

        assert result == request
        assert result.messages[0]["content"] == "hello world"
        assert result.messages[1]["content"] == "hello back"


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    async def test_special_regex_chars(self, policy_context):
        """Test that special regex characters are handled correctly."""
        policy = StringReplacementPolicy(replacements=[["[test]", "check"]])
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="This is [test] data", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "This is check data"

    async def test_overlapping_patterns(self, policy_context):
        """Test behavior with overlapping replacement patterns."""
        # Note: replacements are applied in order, so "ab" is replaced first
        policy = StringReplacementPolicy(
            replacements=[["ab", "x"], ["bc", "y"]],
            match_capitalization=False,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="abc", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        # "ab" replaced first -> "xc", then "bc" doesn't match
        assert result.choices[0].message.content == "xc"

    async def test_unicode_content(self, policy_context):
        """Test that unicode content is handled correctly."""
        policy = StringReplacementPolicy(
            replacements=[["hello", "goodbye"]],
            match_capitalization=True,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="Hello! \U0001f44b", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "Goodbye! \U0001f44b"

    async def test_empty_replacement_value(self, policy_context):
        """Test replacement with empty string (deletion)."""
        policy = StringReplacementPolicy(
            replacements=[["world", ""]],
            match_capitalization=False,
        )
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="hello world", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)
        assert result.choices[0].message.content == "hello "
