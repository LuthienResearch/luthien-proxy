"""Unit tests for AllCapsPolicy."""

import asyncio

import pytest
from litellm.types.utils import Choices, Delta, Message, ModelResponse, StreamingChoices

from luthien_proxy.messages import Request
from luthien_proxy.observability.context import NoOpObservabilityContext
from luthien_proxy.policies.all_caps_policy import AllCapsPolicy
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_state import StreamState


@pytest.fixture
def policy():
    """Create an AllCapsPolicy instance."""
    return AllCapsPolicy()


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
    # Create a real asyncio.Queue instead of a mock for simplicity
    egress_queue = asyncio.Queue()
    return StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=egress_queue,
        original_streaming_response_state=stream_state,
        observability=NoOpObservabilityContext(),
        keepalive=lambda: None,
    )


class TestAllCapsPolicyNonStreaming:
    """Test non-streaming response handling."""

    async def test_uppercase_text_response(self, policy, policy_context):
        """Test that text content is converted to uppercase."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="hello world",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)

        assert result.choices[0].message.content == "HELLO WORLD"

    async def test_uppercase_multiple_choices(self, policy, policy_context):
        """Test that all choices are converted to uppercase."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="hello world",
                        role="assistant",
                    ),
                ),
                Choices(
                    finish_reason="stop",
                    index=1,
                    message=Message(
                        content="goodbye world",
                        role="assistant",
                    ),
                ),
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)

        assert result.choices[0].message.content == "HELLO WORLD"
        assert result.choices[1].message.content == "GOODBYE WORLD"

    async def test_already_uppercase_unchanged(self, policy, policy_context):
        """Test that already uppercase content is unchanged."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="HELLO WORLD",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)

        assert result.choices[0].message.content == "HELLO WORLD"

    async def test_empty_content(self, policy, policy_context):
        """Test that empty content is handled gracefully."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content=None,
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)

        assert result.choices[0].message.content is None

    async def test_no_choices(self, policy, policy_context):
        """Test that response with no choices is unchanged."""
        response = ModelResponse(
            id="test-id",
            choices=[],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)

        assert len(result.choices) == 0


class TestAllCapsPolicyStreaming:
    """Test streaming response handling."""

    async def test_uppercase_content_delta(self, policy, streaming_context):
        """Test that content delta is converted to uppercase."""
        # Create a content delta chunk
        chunk = ModelResponse(
            id="test-id",
            choices=[
                StreamingChoices(
                    finish_reason=None,
                    index=0,
                    delta=Delta(
                        content="hello world",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion.chunk",
        )

        # Add chunk to state
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        # Process the chunk
        await policy.on_content_delta(streaming_context)

        # Check that content was uppercased
        assert chunk.choices[0].delta.content == "HELLO WORLD"

        # Verify chunk was pushed to egress queue
        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk

    async def test_empty_content_delta(self, policy, streaming_context):
        """Test that empty content delta is handled gracefully."""
        # Create a content delta chunk with no content
        chunk = ModelResponse(
            id="test-id",
            choices=[
                StreamingChoices(
                    finish_reason=None,
                    index=0,
                    delta=Delta(
                        content=None,
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion.chunk",
        )

        # Add chunk to state
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        # Process the chunk
        await policy.on_content_delta(streaming_context)

        # Verify chunk was still pushed (even though no transformation)
        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk

    async def test_tool_call_delta_unchanged(self, policy, streaming_context):
        """Test that tool call deltas are passed through unchanged."""
        # Create a tool call delta chunk
        chunk = ModelResponse(
            id="test-id",
            choices=[
                StreamingChoices(
                    finish_reason=None,
                    index=0,
                    delta=Delta(
                        tool_calls=[
                            {
                                "index": 0,
                                "id": "call_123",
                                "function": {"name": "get_weather", "arguments": '{"location": "'},
                                "type": "function",
                            }
                        ],
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion.chunk",
        )

        # Add chunk to state
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        # Process the chunk
        await policy.on_tool_call_delta(streaming_context)

        # Verify chunk was pushed unchanged
        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk
        # Tool call should not be modified
        assert chunk.choices[0].delta.tool_calls[0]["function"]["name"] == "get_weather"

    async def test_content_complete_hook(self, policy, streaming_context):
        """Test that on_content_complete hook exists and doesn't break."""
        # This hook should exist but do nothing (pass-through)
        await policy.on_content_complete(streaming_context)
        # Just verify it doesn't raise an exception

    async def test_finish_reason_hook(self, policy, streaming_context):
        """Test that on_finish_reason hook exists and doesn't break."""
        # This hook should exist but do nothing (pass-through)
        await policy.on_finish_reason(streaming_context)
        # Just verify it doesn't raise an exception

    async def test_stream_complete_hook(self, policy, streaming_context):
        """Test that on_stream_complete hook exists and doesn't break."""
        # This hook should exist but do nothing
        await policy.on_stream_complete(streaming_context)
        # Just verify it doesn't raise an exception

    async def test_multiple_content_deltas(self, policy, streaming_context):
        """Test processing multiple content delta chunks in sequence."""
        chunks_and_expected = [
            ("Hello ", "HELLO "),
            ("world", "WORLD"),
            ("!", "!"),
        ]

        for original, expected in chunks_and_expected:
            chunk = ModelResponse(
                id="test-id",
                choices=[
                    StreamingChoices(
                        finish_reason=None,
                        index=0,
                        delta=Delta(
                            content=original,
                            role="assistant",
                        ),
                    )
                ],
                created=1234567890,
                model="test-model",
                object="chat.completion.chunk",
            )

            streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
            await policy.on_content_delta(streaming_context)

            assert chunk.choices[0].delta.content == expected

    async def test_mixed_case_content(self, policy, streaming_context):
        """Test various mixed case scenarios."""
        test_cases = [
            ("HeLLo WoRLd", "HELLO WORLD"),
            ("123 abc 456", "123 ABC 456"),
            ("test@example.com", "TEST@EXAMPLE.COM"),
            ("CamelCaseText", "CAMELCASETEXT"),
        ]

        for original, expected in test_cases:
            chunk = ModelResponse(
                id="test-id",
                choices=[
                    StreamingChoices(
                        finish_reason=None,
                        index=0,
                        delta=Delta(
                            content=original,
                            role="assistant",
                        ),
                    )
                ],
                created=1234567890,
                model="test-model",
                object="chat.completion.chunk",
            )

            streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
            await policy.on_content_delta(streaming_context)

            assert chunk.choices[0].delta.content == expected


class TestAllCapsPolicyRequest:
    """Test request handling."""

    async def test_request_unchanged(self, policy, policy_context):
        """Test that requests are passed through unchanged."""
        request = Request(
            model="test-model",
            messages=[
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "goodbye world"},
            ],
            max_tokens=100,
            temperature=0.7,
        )

        result = await policy.on_request(request, policy_context)

        assert result == request
        assert result.messages[0]["content"] == "hello world"
        assert result.messages[1]["content"] == "goodbye world"


class TestAllCapsPolicyInvariants:
    """Test policy invariants and edge cases."""

    def test_policy_name(self, policy):
        """Test that policy has a readable name."""
        assert policy.short_policy_name == "AllCapsPolicy"

    async def test_special_characters_preserved(self, policy, policy_context):
        """Test that special characters and formatting are preserved."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="Hello, world!\n\tNew line here: 123 + 456 = 579",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, policy_context)

        # Uppercase but preserve newlines, tabs, punctuation, numbers
        assert result.choices[0].message.content == "HELLO, WORLD!\n\tNEW LINE HERE: 123 + 456 = 579"
