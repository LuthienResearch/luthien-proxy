# ABOUTME: Unit tests for AnthropicExecutionInterface protocol

"""Unit tests for AnthropicExecutionInterface protocol."""

from collections.abc import AsyncIterator
from typing import cast

import pytest

from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
    AnthropicPolicyIOProtocol,
)


class _StubIO(AnthropicPolicyIOProtocol):
    def __init__(self, request: dict):
        self._request = request
        self._first_backend_response: AnthropicResponse | None = None

    @property
    def request(self) -> dict:
        return self._request

    def set_request(self, request: dict) -> None:
        self._request = request

    @property
    def first_backend_response(self) -> AnthropicResponse | None:
        return self._first_backend_response

    async def complete(self, request: dict | None = None) -> AnthropicResponse:
        response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        self._first_backend_response = response
        return response

    def stream(self, request: dict | None = None) -> AsyncIterator:
        async def _stream() -> AsyncIterator:
            if False:
                yield None

        return _stream()


class TestAnthropicExecutionInterface:
    def test_runtime_checkable_protocol_positive(self):
        class CompletePolicy:
            def run_anthropic(self, io, context):
                async def _run():
                    yield await io.complete(io.request)

                return _run()

        assert isinstance(CompletePolicy(), AnthropicExecutionInterface)

    def test_runtime_checkable_protocol_negative(self):
        class IncompletePolicy:
            pass

        assert not isinstance(IncompletePolicy(), AnthropicExecutionInterface)

    @pytest.mark.asyncio
    async def test_run_anthropic_emits_response(self):
        class TestPolicy:
            def run_anthropic(self, io, context):
                async def _run():
                    yield await io.complete(io.request)

                return _run()

        policy = cast(AnthropicExecutionInterface, TestPolicy())
        io = _StubIO(request={"model": "claude-sonnet-4-20250514", "messages": [], "max_tokens": 1})

        emissions = [emission async for emission in policy.run_anthropic(io, context={})]
        assert len(emissions) == 1
        assert isinstance(emissions[0], dict)
        assert emissions[0]["type"] == "message"
