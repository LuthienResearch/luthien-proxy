"""Unit tests for AnthropicExecutionInterface protocol."""

from typing import cast

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import RawContentBlockStartEvent

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestAnthropicExecutionInterface:
    def test_runtime_checkable_protocol_positive(self):
        class CompletePolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_anthropic_response(
                self, response: AnthropicResponse, context: PolicyContext
            ) -> AnthropicResponse:
                return response

            async def on_anthropic_stream_event(
                self, event: MessageStreamEvent, context: PolicyContext
            ) -> list[MessageStreamEvent]:
                return [event]

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
                return []

        assert isinstance(CompletePolicy(), AnthropicExecutionInterface)

    def test_text_modifier_policy_satisfies_protocol(self):
        """TextModifierPolicy satisfies AnthropicExecutionInterface structurally (no explicit inheritance)."""
        from luthien_proxy.policy_core.text_modifier_policy import TextModifierPolicy

        assert isinstance(TextModifierPolicy(), AnthropicExecutionInterface)

    def test_runtime_checkable_protocol_negative(self):
        class IncompletePolicy:
            pass

        assert not isinstance(IncompletePolicy(), AnthropicExecutionInterface)

    def test_runtime_checkable_protocol_partial_negative(self):
        class PartialPolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

        assert not isinstance(PartialPolicy(), AnthropicExecutionInterface)

    @pytest.mark.asyncio
    async def test_on_anthropic_request_hook(self):
        class TestPolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                request["modified"] = True
                return request

            async def on_anthropic_response(
                self, response: AnthropicResponse, context: PolicyContext
            ) -> AnthropicResponse:
                return response

            async def on_anthropic_stream_event(
                self, event: MessageStreamEvent, context: PolicyContext
            ) -> list[MessageStreamEvent]:
                return [event]

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
                return []

        policy = cast(AnthropicExecutionInterface, TestPolicy())
        request: AnthropicRequest = {"model": DEFAULT_TEST_MODEL, "messages": [], "max_tokens": 1}

        result = await policy.on_anthropic_request(request, PolicyContext.for_testing())
        assert result.get("modified") is True

    @pytest.mark.asyncio
    async def test_on_anthropic_response_hook(self):
        class TestPolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_anthropic_response(
                self, response: AnthropicResponse, context: PolicyContext
            ) -> AnthropicResponse:
                return response

            async def on_anthropic_stream_event(
                self, event: MessageStreamEvent, context: PolicyContext
            ) -> list[MessageStreamEvent]:
                return [event]

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
                return []

        policy = cast(AnthropicExecutionInterface, TestPolicy())
        response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

        result = await policy.on_anthropic_response(response, PolicyContext.for_testing())
        assert result["type"] == "message"

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_hook(self):
        class TestPolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_anthropic_response(
                self, response: AnthropicResponse, context: PolicyContext
            ) -> AnthropicResponse:
                return response

            async def on_anthropic_stream_event(
                self, event: MessageStreamEvent, context: PolicyContext
            ) -> list[MessageStreamEvent]:
                return [event, event]

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
                return []

        policy = cast(AnthropicExecutionInterface, TestPolicy())
        event = RawContentBlockStartEvent(
            type="content_block_start", index=0, content_block={"type": "text", "text": ""}
        )

        result = await policy.on_anthropic_stream_event(event, PolicyContext.for_testing())
        assert len(result) == 2
        assert result[0].type == "content_block_start"

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_complete_hook(self):
        class TestPolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_anthropic_response(
                self, response: AnthropicResponse, context: PolicyContext
            ) -> AnthropicResponse:
                return response

            async def on_anthropic_stream_event(
                self, event: MessageStreamEvent, context: PolicyContext
            ) -> list[MessageStreamEvent]:
                return [event]

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
                return []

        policy = cast(AnthropicExecutionInterface, TestPolicy())
        result = await policy.on_anthropic_stream_complete(PolicyContext.for_testing())
        assert result == []
