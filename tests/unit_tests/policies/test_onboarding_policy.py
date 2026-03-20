"""Unit tests for OnboardingPolicy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.policies.onboarding_policy import (
    OnboardingPolicy,
    OnboardingPolicyConfig,
    is_first_turn,
)
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    BasePolicy,
    TextModifierPolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture
def policy():
    return OnboardingPolicy({"gateway_url": "http://localhost:9999"})


@pytest.fixture
def context():
    return PolicyContext.for_testing()


# =============================================================================
# is_first_turn tests
# =============================================================================


class TestIsFirstTurn:
    def test_single_user_message(self):
        request = {"messages": [{"role": "user", "content": "hello"}]}
        assert is_first_turn(request) is True

    def test_conversation_with_assistant_response(self):
        request = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
                {"role": "user", "content": "how are you"},
            ]
        }
        assert is_first_turn(request) is False

    def test_empty_messages(self):
        assert is_first_turn({"messages": []}) is False

    def test_no_messages_key(self):
        assert is_first_turn({}) is False

    def test_system_message_with_single_user(self):
        """System messages don't count as user or assistant turns."""
        request = {
            "messages": [
                {"role": "user", "content": "hello"},
            ]
        }
        assert is_first_turn(request) is True

    def test_multiple_user_messages_no_assistant(self):
        """Edge case: multiple user messages but no assistant — not first turn."""
        request = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "user", "content": "are you there?"},
            ]
        }
        assert is_first_turn(request) is False


# =============================================================================
# Protocol compliance
# =============================================================================


class TestProtocol:
    def test_inherits_text_modifier(self, policy):
        assert isinstance(policy, TextModifierPolicy)

    def test_inherits_base_policy(self, policy):
        assert isinstance(policy, BasePolicy)

    def test_implements_anthropic_interface(self, policy):
        assert isinstance(policy, AnthropicExecutionInterface)


# =============================================================================
# Config
# =============================================================================


class TestConfig:
    def test_default_gateway_url(self):
        policy = OnboardingPolicy()
        assert policy._gateway_url == "http://localhost:8000"

    def test_custom_gateway_url(self, policy):
        assert policy._gateway_url == "http://localhost:9999"

    def test_trailing_slash_stripped(self):
        policy = OnboardingPolicy({"gateway_url": "http://localhost:8000/"})
        assert policy._gateway_url == "http://localhost:8000"

    def test_config_from_pydantic(self):
        config = OnboardingPolicyConfig(gateway_url="http://example.com")
        policy = OnboardingPolicy(config)
        assert policy._gateway_url == "http://example.com"


# =============================================================================
# Welcome message content
# =============================================================================


class TestWelcomeMessage:
    def test_contains_config_url(self, policy):
        assert "http://localhost:9999/policy-config" in policy._welcome

    def test_contains_luthien_branding(self, policy):
        assert "Luthien" in policy._welcome

    def test_extra_text_returns_welcome(self, policy):
        assert policy.extra_text() == policy._welcome


# =============================================================================
# Non-streaming response (via hook interface for MultiSerialPolicy)
# =============================================================================


class TestNonStreamingResponse:
    @pytest.mark.asyncio
    async def test_first_turn_appends_welcome(self, policy, context):
        """On first turn, welcome text is appended to response content."""
        context.request = {"messages": [{"role": "user", "content": "hi"}]}
        response = {
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        content_blocks = result["content"]
        assert len(content_blocks) == 2
        assert content_blocks[0]["text"] == "Hello!"
        assert "Luthien" in content_blocks[1]["text"]

    @pytest.mark.asyncio
    async def test_subsequent_turn_passthrough(self, policy, context):
        """On subsequent turns, response passes through unchanged."""
        context.request = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how are you"},
            ]
        }
        response = {
            "content": [{"type": "text", "text": "I'm fine!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        assert len(result["content"]) == 1
        assert result["content"][0]["text"] == "I'm fine!"


# =============================================================================
# Streaming (via hook interface)
# =============================================================================


class TestStreamingHooks:
    @pytest.mark.asyncio
    async def test_stream_complete_emits_welcome_on_first_turn(self, policy, context):
        """on_anthropic_stream_complete emits welcome block events on first turn."""
        context.request = {"messages": [{"role": "user", "content": "hi"}]}

        # Simulate a content block start so TextModifierPolicy tracks max_index
        from anthropic.types import RawContentBlockStartEvent, TextBlock

        start_event = RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_event, context)

        events = await policy.on_anthropic_stream_complete(context)
        # TextModifierPolicy emits 3 events: block_start, delta, block_stop
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_stream_complete_empty_on_subsequent_turn(self, policy, context):
        """on_anthropic_stream_complete returns empty on subsequent turns."""
        context.request = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "more"},
            ]
        }
        events = await policy.on_anthropic_stream_complete(context)
        assert events == []


# =============================================================================
# run_anthropic (direct execution path)
# =============================================================================


class TestRunAnthropic:
    @pytest.mark.asyncio
    async def test_passthrough_on_subsequent_turn(self, policy):
        """On subsequent turns, run_anthropic passes through without modification."""
        io = MagicMock()
        io.request = {
            "stream": False,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "more"},
            ],
        }
        io.complete = AsyncMock(
            return_value={
                "content": [{"type": "text", "text": "response"}],
                "model": "test",
                "role": "assistant",
            }
        )
        context = PolicyContext.for_testing()

        results = []
        async for emission in policy.run_anthropic(io, context):
            results.append(emission)

        assert len(results) == 1
        assert results[0]["content"][0]["text"] == "response"
