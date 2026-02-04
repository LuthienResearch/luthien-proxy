# ABOUTME: Tests for AnthropicStreamExecutor that processes Anthropic streaming events through policy hooks
# ABOUTME: Verifies passthrough, filtering, transformation, and error handling behaviors

"""Tests for AnthropicStreamExecutor.

Verifies that the executor correctly:
- Passes events through with NoOp-like policies
- Filters events when policy returns None
- Transforms events when policy returns modified events
- Handles errors gracefully without crashing the stream
"""

import logging
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest

from luthien_proxy.policy_core.anthropic_protocol import (
    AnthropicPolicyProtocol,
    AnthropicStreamEvent,
    ContentBlockDelta,
    ContentBlockStart,
    ContentBlockStop,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextDelta,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.anthropic_executor import AnthropicStreamExecutor

# =============================================================================
# Test Fixtures and Helpers
# =============================================================================


async def async_iter_from_list(items: list[AnthropicStreamEvent]) -> AsyncIterator[AnthropicStreamEvent]:
    """Convert a list to an async iterator."""
    for item in items:
        yield item


def make_message_start_event(message_id: str = "msg_test") -> MessageStart:
    """Create a message_start event for testing."""
    return {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        },
    }


def make_content_block_start_event(index: int = 0) -> ContentBlockStart:
    """Create a content_block_start event for testing."""
    return {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""},
    }


def make_text_delta_event(text: str, index: int = 0) -> ContentBlockDelta:
    """Create a content_block_delta event with text for testing."""
    delta: TextDelta = {"type": "text_delta", "text": text}
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": delta,
    }


def make_content_block_stop_event(index: int = 0) -> ContentBlockStop:
    """Create a content_block_stop event for testing."""
    return {"type": "content_block_stop", "index": index}


def make_message_delta_event(stop_reason: str = "end_turn") -> MessageDelta:
    """Create a message_delta event for testing."""
    return {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},  # type: ignore[typeddict-item]
        "usage": {"output_tokens": 10},
    }


def make_message_stop_event() -> MessageStop:
    """Create a message_stop event for testing."""
    return {"type": "message_stop"}


@pytest.fixture
def policy_ctx() -> PolicyContext:
    """Create a PolicyContext for testing."""
    return PolicyContext.for_testing()


# =============================================================================
# Mock Policies for Testing
# =============================================================================


class PassthroughPolicy:
    """Policy that passes all events through unchanged."""

    @property
    def short_policy_name(self) -> str:
        return "Passthrough"

    async def on_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_stream_event(self, event: AnthropicStreamEvent, context: PolicyContext) -> AnthropicStreamEvent | None:
        return event


class FilteringPolicy:
    """Policy that filters out events containing specific text."""

    def __init__(self, filter_text: str = "secret"):
        self.filter_text = filter_text

    @property
    def short_policy_name(self) -> str:
        return "Filtering"

    async def on_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_stream_event(self, event: AnthropicStreamEvent, context: PolicyContext) -> AnthropicStreamEvent | None:
        if event.get("type") == "content_block_delta":
            event_dict = cast(dict[str, Any], event)
            delta = event_dict.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if self.filter_text in text.lower():
                    return None
        return event


class TransformingPolicy:
    """Policy that transforms text deltas to uppercase."""

    @property
    def short_policy_name(self) -> str:
        return "Transforming"

    async def on_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_stream_event(self, event: AnthropicStreamEvent, context: PolicyContext) -> AnthropicStreamEvent | None:
        if event.get("type") == "content_block_delta":
            event_dict = cast(ContentBlockDelta, event)
            delta = event_dict["delta"]
            if delta.get("type") == "text_delta":
                text_delta = cast(TextDelta, delta)
                new_delta: TextDelta = {
                    "type": "text_delta",
                    "text": text_delta["text"].upper(),
                }
                result: ContentBlockDelta = {
                    "type": "content_block_delta",
                    "index": event_dict["index"],
                    "delta": new_delta,
                }
                return result
        return event


class ErrorThrowingPolicy:
    """Policy that throws an error on specific events."""

    def __init__(self, error_on_text: str = "error"):
        self.error_on_text = error_on_text

    @property
    def short_policy_name(self) -> str:
        return "ErrorThrowing"

    async def on_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_stream_event(self, event: AnthropicStreamEvent, context: PolicyContext) -> AnthropicStreamEvent | None:
        if event.get("type") == "content_block_delta":
            event_dict = cast(dict[str, Any], event)
            delta = event_dict.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if self.error_on_text in text.lower():
                    raise ValueError(f"Policy error on text: {text}")
        return event


# =============================================================================
# Tests for Basic Passthrough
# =============================================================================


class TestAnthropicStreamExecutorPassthrough:
    """Tests for basic passthrough behavior."""

    @pytest.mark.asyncio
    async def test_single_event_passthrough(self, policy_ctx: PolicyContext):
        """Test that a single event passes through unchanged."""
        executor = AnthropicStreamExecutor()
        policy = PassthroughPolicy()

        event = make_text_delta_event("Hello")
        stream = async_iter_from_list([event])

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0] == event

    @pytest.mark.asyncio
    async def test_multiple_events_passthrough(self, policy_ctx: PolicyContext):
        """Test that multiple events pass through in order."""
        executor = AnthropicStreamExecutor()
        policy = PassthroughPolicy()

        events: list[AnthropicStreamEvent] = [
            make_message_start_event(),
            make_content_block_start_event(),
            make_text_delta_event("Hello"),
            make_text_delta_event(" world"),
            make_content_block_stop_event(),
            make_message_delta_event(),
            make_message_stop_event(),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == len(events)
        for i, result in enumerate(results):
            assert result == events[i]

    @pytest.mark.asyncio
    async def test_empty_stream_produces_no_output(self, policy_ctx: PolicyContext):
        """Test that empty stream produces no output."""
        executor = AnthropicStreamExecutor()
        policy = PassthroughPolicy()

        stream = async_iter_from_list([])

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 0


# =============================================================================
# Tests for Event Filtering
# =============================================================================


class TestAnthropicStreamExecutorFiltering:
    """Tests for event filtering behavior."""

    @pytest.mark.asyncio
    async def test_filter_specific_events(self, policy_ctx: PolicyContext):
        """Test that events can be filtered out by returning None."""
        executor = AnthropicStreamExecutor()
        policy = FilteringPolicy(filter_text="secret")

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("Hello"),
            make_text_delta_event("This is secret"),
            make_text_delta_event("World"),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 2
        delta1 = cast(ContentBlockDelta, results[0])
        delta2 = cast(ContentBlockDelta, results[1])
        assert cast(TextDelta, delta1["delta"])["text"] == "Hello"
        assert cast(TextDelta, delta2["delta"])["text"] == "World"

    @pytest.mark.asyncio
    async def test_filter_all_events(self, policy_ctx: PolicyContext):
        """Test that all events can be filtered out."""
        executor = AnthropicStreamExecutor()
        policy = FilteringPolicy(filter_text="x")  # matches all

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("text x"),
            make_text_delta_event("x text"),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_non_text_events_pass_through_filtering_policy(self, policy_ctx: PolicyContext):
        """Test that non-text events pass through a text-filtering policy."""
        executor = AnthropicStreamExecutor()
        policy = FilteringPolicy(filter_text="secret")

        events: list[AnthropicStreamEvent] = [
            make_message_start_event(),
            make_content_block_start_event(),
            make_content_block_stop_event(),
            make_message_stop_event(),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 4


# =============================================================================
# Tests for Event Transformation
# =============================================================================


class TestAnthropicStreamExecutorTransformation:
    """Tests for event transformation behavior."""

    @pytest.mark.asyncio
    async def test_transform_text_delta(self, policy_ctx: PolicyContext):
        """Test that events can be transformed by the policy."""
        executor = AnthropicStreamExecutor()
        policy = TransformingPolicy()

        events: list[AnthropicStreamEvent] = [make_text_delta_event("hello")]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 1
        delta = cast(ContentBlockDelta, results[0])
        text_delta = cast(TextDelta, delta["delta"])
        assert text_delta["text"] == "HELLO"

    @pytest.mark.asyncio
    async def test_transform_preserves_non_text_events(self, policy_ctx: PolicyContext):
        """Test that non-text events pass through unchanged by transforming policy."""
        executor = AnthropicStreamExecutor()
        policy = TransformingPolicy()

        events: list[AnthropicStreamEvent] = [
            make_message_start_event("msg_123"),
            make_content_block_start_event(),
            make_text_delta_event("hello"),
            make_content_block_stop_event(),
            make_message_stop_event(),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 5
        # Check message_start is unchanged
        msg_start = cast(MessageStart, results[0])
        assert msg_start["message"]["id"] == "msg_123"
        # Check text delta is transformed
        delta = cast(ContentBlockDelta, results[2])
        text_delta = cast(TextDelta, delta["delta"])
        assert text_delta["text"] == "HELLO"


# =============================================================================
# Tests for Error Handling
# =============================================================================


class TestAnthropicStreamExecutorErrorHandling:
    """Tests for error handling behavior."""

    @pytest.mark.asyncio
    async def test_policy_error_does_not_crash_stream(self, policy_ctx: PolicyContext, caplog):
        """Test that errors in policy don't crash the stream."""
        executor = AnthropicStreamExecutor()
        policy = ErrorThrowingPolicy(error_on_text="error")

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("Hello"),
            make_text_delta_event("This triggers error"),
            make_text_delta_event("World"),
        ]
        stream = async_iter_from_list(events)

        results = []
        with caplog.at_level(logging.WARNING):
            async for result in executor.process(stream, policy, policy_ctx):
                results.append(result)

        # Error event should be skipped, others should pass through
        assert len(results) == 2
        delta1 = cast(ContentBlockDelta, results[0])
        delta2 = cast(ContentBlockDelta, results[1])
        assert cast(TextDelta, delta1["delta"])["text"] == "Hello"
        assert cast(TextDelta, delta2["delta"])["text"] == "World"

        # Check that the error was logged
        assert "Error in policy on_stream_event" in caplog.text

    @pytest.mark.asyncio
    async def test_policy_error_on_first_event(self, policy_ctx: PolicyContext, caplog):
        """Test that error on first event doesn't prevent rest of stream."""
        executor = AnthropicStreamExecutor()
        policy = ErrorThrowingPolicy(error_on_text="error")

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("error first"),
            make_text_delta_event("second"),
        ]
        stream = async_iter_from_list(events)

        results = []
        with caplog.at_level(logging.WARNING):
            async for result in executor.process(stream, policy, policy_ctx):
                results.append(result)

        assert len(results) == 1
        delta = cast(ContentBlockDelta, results[0])
        assert cast(TextDelta, delta["delta"])["text"] == "second"


# =============================================================================
# Tests for Protocol Compliance
# =============================================================================


class TestAnthropicStreamExecutorProtocolCompliance:
    """Tests verifying correct interaction with AnthropicPolicyProtocol."""

    @pytest.mark.asyncio
    async def test_policy_receives_context(self, policy_ctx: PolicyContext):
        """Test that policy receives the correct context."""

        class ContextTrackingPolicy:
            def __init__(self):
                self.received_contexts: list[PolicyContext] = []

            @property
            def short_policy_name(self) -> str:
                return "ContextTracking"

            async def on_request(self, request: Any, context: PolicyContext) -> Any:
                return request

            async def on_response(self, response: Any, context: PolicyContext) -> Any:
                return response

            async def on_stream_event(
                self, event: AnthropicStreamEvent, context: PolicyContext
            ) -> AnthropicStreamEvent | None:
                self.received_contexts.append(context)
                return event

        executor = AnthropicStreamExecutor()
        policy = ContextTrackingPolicy()

        events: list[AnthropicStreamEvent] = [make_text_delta_event("test")]
        stream = async_iter_from_list(events)

        async for _ in executor.process(stream, policy, policy_ctx):
            pass

        assert len(policy.received_contexts) == 1
        assert policy.received_contexts[0] is policy_ctx

    @pytest.mark.asyncio
    async def test_passthrough_policy_implements_protocol(self):
        """Test that our test policies implement the protocol."""
        policy = PassthroughPolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    @pytest.mark.asyncio
    async def test_filtering_policy_implements_protocol(self):
        """Test that filtering policy implements the protocol."""
        policy = FilteringPolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    @pytest.mark.asyncio
    async def test_transforming_policy_implements_protocol(self):
        """Test that transforming policy implements the protocol."""
        policy = TransformingPolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)
