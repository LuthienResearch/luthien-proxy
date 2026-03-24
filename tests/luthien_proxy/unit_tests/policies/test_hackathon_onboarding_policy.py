"""Unit tests for HackathonOnboardingPolicy."""

from __future__ import annotations

import pytest

from luthien_proxy.policies.hackathon_onboarding_policy import (
    HackathonOnboardingPolicy,
    HackathonOnboardingPolicyConfig,
)
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    BasePolicy,
    TextModifierPolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture
def policy():
    return HackathonOnboardingPolicy({"gateway_url": "http://localhost:9999"})


@pytest.fixture
def context():
    return PolicyContext.for_testing()


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
        policy = HackathonOnboardingPolicy()
        assert policy._gateway_url == "http://localhost:8000"

    def test_custom_gateway_url(self, policy):
        assert policy._gateway_url == "http://localhost:9999"

    def test_trailing_slash_stripped(self):
        policy = HackathonOnboardingPolicy({"gateway_url": "http://localhost:8000/"})
        assert policy._gateway_url == "http://localhost:8000"

    def test_config_from_pydantic(self):
        config = HackathonOnboardingPolicyConfig(gateway_url="http://example.com")
        policy = HackathonOnboardingPolicy(config)
        assert policy._gateway_url == "http://example.com"


# =============================================================================
# Welcome message content
# =============================================================================


class TestWelcomeMessage:
    def test_contains_policy_config_url(self, policy):
        assert "http://localhost:9999/policy-config" in policy._welcome

    def test_contains_activity_monitor_url(self, policy):
        assert "http://localhost:9999/activity" in policy._welcome

    def test_contains_hackathon_branding(self, policy):
        assert "Hackathon" in policy._welcome

    def test_contains_luthien_branding(self, policy):
        assert "Luthien" in policy._welcome

    def test_mentions_key_files(self, policy):
        assert "hackathon_policy_template.py" in policy._welcome
        assert "all_caps_policy.py" in policy._welcome
        assert "text_modifier_policy.py" in policy._welcome

    def test_mentions_top_project_ideas(self, policy):
        assert "Resampling" in policy._welcome
        assert "Trusted model reroute" in policy._welcome
        assert "Proxy commands" in policy._welcome
        assert "Live policy editor" in policy._welcome
        assert "Character injection" in policy._welcome

    def test_extra_text_returns_welcome(self, policy):
        assert policy.extra_text() == policy._welcome


# =============================================================================
# Non-streaming response (via hook interface for MultiSerialPolicy)
# =============================================================================


class TestNonStreamingResponse:
    @pytest.mark.asyncio
    async def test_first_turn_appends_welcome(self, policy, context):
        """On first turn, welcome text is appended to response content."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)
        response = {
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        content_blocks = result["content"]
        assert len(content_blocks) == 1
        assert content_blocks[0]["text"].startswith("Hello!")
        assert "Hackathon" in content_blocks[0]["text"]

    @pytest.mark.asyncio
    async def test_subsequent_turn_passthrough(self, policy, context):
        """On subsequent turns, response passes through unchanged."""
        request = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how are you"},
            ]
        }
        await policy.on_anthropic_request(request, context)
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
        """on_anthropic_stream_complete injects suffix delta + flushes held stop."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)

        from anthropic.types import (
            RawContentBlockDeltaEvent,
            RawContentBlockStartEvent,
            RawContentBlockStopEvent,
            TextBlock,
            TextDelta,
        )

        start_event = RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text=""),
        )
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=0)

        await policy.on_anthropic_stream_event(start_event, context)
        await policy.on_anthropic_stream_event(stop_event, context)

        events = await policy.on_anthropic_stream_complete(context)
        # suffix text_delta + held content_block_stop
        assert len(events) == 2
        assert isinstance(events[0], RawContentBlockDeltaEvent)
        assert isinstance(events[0].delta, TextDelta)
        assert "Hackathon" in events[0].delta.text
        assert isinstance(events[1], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_stream_complete_empty_on_subsequent_turn(self, policy, context):
        """on_anthropic_stream_complete returns empty on subsequent turns."""
        request = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "more"},
            ]
        }
        await policy.on_anthropic_request(request, context)
        events = await policy.on_anthropic_stream_complete(context)
        assert events == []


