# ABOUTME: Tests for AnthropicPolicyProtocol defining native Anthropic policy interface

"""Tests for AnthropicPolicyProtocol.

Verifies that policies can implement the Anthropic-native protocol for:
- Non-streaming request/response processing
- Streaming event processing with filtering/transformation
"""

from typing import Any, cast

import pytest

from luthien_proxy.llm.types.anthropic import (
    AnthropicContentBlockDeltaEvent,
    AnthropicContentBlockStartEvent,
    AnthropicContentBlockStopEvent,
    AnthropicMessageDeltaEvent,
    AnthropicMessageStartEvent,
    AnthropicMessageStopEvent,
    AnthropicRequest,
    AnthropicResponse,
    AnthropicStreamingEvent,
    AnthropicTextBlock,
    AnthropicTextDelta,
)
from luthien_proxy.policy_core.anthropic_protocol import AnthropicPolicyProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestAnthropicPolicyProtocolNonStreaming:
    """Tests for non-streaming request/response hooks."""

    def test_mock_policy_implements_protocol(self):
        """A class implementing all hooks satisfies the protocol."""

        class MockAnthropicPolicy:
            @property
            def short_policy_name(self) -> str:
                return "MockPolicy"

            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
                return response

            async def on_stream_event(
                self, event: AnthropicStreamingEvent, context: PolicyContext
            ) -> AnthropicStreamingEvent | None:
                return event

        policy = MockAnthropicPolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    def test_policy_without_short_name_fails_protocol(self):
        """A class missing short_policy_name doesn't satisfy protocol."""

        class IncompletePolicy:
            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

        policy = IncompletePolicy()
        assert not isinstance(policy, AnthropicPolicyProtocol)

    @pytest.mark.asyncio
    async def test_on_request_can_modify_request(self):
        """on_request can transform the request before it goes to the LLM."""

        class RequestModifyingPolicy:
            @property
            def short_policy_name(self) -> str:
                return "RequestModifier"

            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                modified: dict[str, Any] = dict(request)
                modified["temperature"] = 0.5
                return cast(AnthropicRequest, modified)

            async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
                return response

            async def on_stream_event(
                self, event: AnthropicStreamingEvent, context: PolicyContext
            ) -> AnthropicStreamingEvent | None:
                return event

        policy = RequestModifyingPolicy()
        ctx = PolicyContext.for_testing()
        original_request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        modified = await policy.on_request(original_request, ctx)
        assert modified.get("temperature") == 0.5
        assert modified["model"] == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_on_response_can_modify_response(self):
        """on_response can transform the response before it goes to the client."""

        class ResponseModifyingPolicy:
            @property
            def short_policy_name(self) -> str:
                return "ResponseModifier"

            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
                modified: dict[str, Any] = dict(response)
                content: list[dict[str, Any]] = []
                for block in modified["content"]:
                    block_dict = dict(block)
                    if block_dict.get("type") == "text":
                        block_dict["text"] = block_dict["text"].upper()
                    content.append(block_dict)
                modified["content"] = content
                return cast(AnthropicResponse, modified)

            async def on_stream_event(
                self, event: AnthropicStreamingEvent, context: PolicyContext
            ) -> AnthropicStreamingEvent | None:
                return event

        policy = ResponseModifyingPolicy()
        ctx = PolicyContext.for_testing()
        text_block: AnthropicTextBlock = {"type": "text", "text": "hello world"}
        original_response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        modified = await policy.on_response(original_response, ctx)
        first_block = cast(AnthropicTextBlock, modified["content"][0])
        assert first_block["text"] == "HELLO WORLD"


class TestAnthropicStreamingEvents:
    """Tests for Anthropic streaming event types."""

    def test_message_start_structure(self):
        """AnthropicMessageStartEvent event has correct structure."""
        event: AnthropicMessageStartEvent = {
            "type": "message_start",
            "message": {
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-sonnet-4-20250514",
                "stop_reason": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        }
        assert event["type"] == "message_start"
        assert event["message"]["id"] == "msg_123"

    def test_content_block_start_text(self):
        """AnthropicContentBlockStartEvent for text block."""
        event: AnthropicContentBlockStartEvent = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
        assert event["type"] == "content_block_start"
        assert event["index"] == 0
        assert event["content_block"]["type"] == "text"

    def test_content_block_delta_text(self):
        """AnthropicContentBlockDeltaEvent with text delta."""
        text_delta: AnthropicTextDelta = {"type": "text_delta", "text": "Hello"}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": text_delta,
        }
        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "text_delta"
        delta = cast(AnthropicTextDelta, event["delta"])
        assert delta["text"] == "Hello"

    def test_content_block_stop(self):
        """AnthropicContentBlockStopEvent event."""
        event: AnthropicContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
        assert event["type"] == "content_block_stop"
        assert event["index"] == 0

    def test_message_delta(self):
        """AnthropicMessageDeltaEvent with stop reason."""
        event: AnthropicMessageDeltaEvent = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 15},
        }
        assert event["type"] == "message_delta"
        assert event["delta"].get("stop_reason") == "end_turn"

    def test_message_stop(self):
        """AnthropicMessageStopEvent event."""
        event: AnthropicMessageStopEvent = {"type": "message_stop"}
        assert event["type"] == "message_stop"


class TestAnthropicPolicyProtocolStreaming:
    """Tests for streaming event processing."""

    @pytest.mark.asyncio
    async def test_on_stream_event_can_pass_through(self):
        """on_stream_event returning event passes it through unchanged."""

        class PassthroughPolicy:
            @property
            def short_policy_name(self) -> str:
                return "Passthrough"

            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
                return response

            async def on_stream_event(
                self, event: AnthropicStreamingEvent, context: PolicyContext
            ) -> AnthropicStreamingEvent | None:
                return event

        policy = PassthroughPolicy()
        ctx = PolicyContext.for_testing()

        text_delta: AnthropicTextDelta = {"type": "text_delta", "text": "Hello"}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": text_delta,
        }

        result = await policy.on_stream_event(event, ctx)
        assert result == event

    @pytest.mark.asyncio
    async def test_on_stream_event_can_filter(self):
        """on_stream_event returning None filters out the event."""

        class FilteringPolicy:
            @property
            def short_policy_name(self) -> str:
                return "Filter"

            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
                return response

            async def on_stream_event(
                self, event: AnthropicStreamingEvent, context: PolicyContext
            ) -> AnthropicStreamingEvent | None:
                # Filter out text that contains "secret"
                if event.get("type") == "content_block_delta":
                    event_dict = cast(dict[str, Any], event)
                    delta = event_dict.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if "secret" in text.lower():
                            return None
                return event

        policy = FilteringPolicy()
        ctx = PolicyContext.for_testing()

        normal_delta: AnthropicTextDelta = {"type": "text_delta", "text": "Hello"}
        normal_event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": normal_delta,
        }
        secret_delta: AnthropicTextDelta = {"type": "text_delta", "text": "This is secret"}
        secret_event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": secret_delta,
        }

        assert await policy.on_stream_event(normal_event, ctx) == normal_event
        assert await policy.on_stream_event(secret_event, ctx) is None

    @pytest.mark.asyncio
    async def test_on_stream_event_can_transform(self):
        """on_stream_event can modify events before they reach the client."""

        class TransformingPolicy:
            @property
            def short_policy_name(self) -> str:
                return "Transform"

            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
                return response

            async def on_stream_event(
                self, event: AnthropicStreamingEvent, context: PolicyContext
            ) -> AnthropicStreamingEvent | None:
                # Uppercase all text deltas
                if event.get("type") == "content_block_delta":
                    event_dict = cast(AnthropicContentBlockDeltaEvent, event)
                    delta = event_dict["delta"]
                    if delta.get("type") == "text_delta":
                        text_delta = cast(AnthropicTextDelta, delta)
                        new_delta: AnthropicTextDelta = {
                            "type": "text_delta",
                            "text": text_delta["text"].upper(),
                        }
                        result: AnthropicContentBlockDeltaEvent = {
                            "type": "content_block_delta",
                            "index": event_dict["index"],
                            "delta": new_delta,
                        }
                        return result
                return event

        policy = TransformingPolicy()
        ctx = PolicyContext.for_testing()

        text_delta: AnthropicTextDelta = {"type": "text_delta", "text": "hello"}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": text_delta,
        }

        result = await policy.on_stream_event(event, ctx)
        assert result is not None
        result_delta = cast(AnthropicContentBlockDeltaEvent, result)
        delta = cast(AnthropicTextDelta, result_delta["delta"])
        assert delta["text"] == "HELLO"


class TestPolicyContextIntegration:
    """Tests for policy context integration with Anthropic protocol."""

    @pytest.mark.asyncio
    async def test_policy_can_use_scratchpad(self):
        """Policy can store state across events in the scratchpad."""

        class StatefulPolicy:
            @property
            def short_policy_name(self) -> str:
                return "Stateful"

            async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                context.scratchpad["request_seen"] = True
                return request

            async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
                return response

            async def on_stream_event(
                self, event: AnthropicStreamingEvent, context: PolicyContext
            ) -> AnthropicStreamingEvent | None:
                if event.get("type") == "content_block_delta":
                    context.scratchpad.setdefault("delta_count", 0)
                    context.scratchpad["delta_count"] += 1
                return event

        policy = StatefulPolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }
        await policy.on_request(request, ctx)
        assert ctx.scratchpad["request_seen"] is True

        for i in range(3):
            text_delta: AnthropicTextDelta = {"type": "text_delta", "text": f"chunk{i}"}
            event: AnthropicContentBlockDeltaEvent = {
                "type": "content_block_delta",
                "index": 0,
                "delta": text_delta,
            }
            await policy.on_stream_event(event, ctx)

        assert ctx.scratchpad["delta_count"] == 3
