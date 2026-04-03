"""Tests for ConversationLinkPolicy."""

import pytest

from luthien_proxy.policies.conversation_link_policy import ConversationLinkPolicy
from luthien_proxy.policy_core.policy_context import PolicyContext


def _first_turn_request():
    """A request that looks like the first turn of a conversation."""
    return {
        "model": "claude-opus-4-6",
        "max_tokens": 8000,
        "stream": True,
        "system": [{"type": "text", "text": "You are a helpful assistant."}],
        "messages": [{"role": "user", "content": "Hello"}],
    }


def _subsequent_turn_request():
    """A request with prior conversation history — not first turn."""
    return {
        "model": "claude-opus-4-6",
        "max_tokens": 8000,
        "stream": True,
        "system": [{"type": "text", "text": "You are a helpful assistant."}],
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ],
    }


class TestConversationLinkPolicy:
    def _make_context(self, session_id: str | None = "test-session") -> PolicyContext:
        return PolicyContext.for_testing(session_id=session_id)

    async def _run_first_turn(
        self, policy: ConversationLinkPolicy, ctx: PolicyContext, content: str = "Hello world"
    ) -> str:
        """Simulate a first-turn request then call simple_on_response_content."""
        await policy.on_anthropic_request(_first_turn_request(), ctx)
        return await policy.simple_on_response_content(content, ctx)

    async def _run_subsequent_turn(
        self, policy: ConversationLinkPolicy, ctx: PolicyContext, content: str = "Hello world"
    ) -> str:
        """Simulate a subsequent-turn request then call simple_on_response_content."""
        await policy.on_anthropic_request(_subsequent_turn_request(), ctx)
        return await policy.simple_on_response_content(content, ctx)

    @pytest.mark.asyncio
    async def test_injects_link_on_first_turn(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess-abc")

        result = await self._run_first_turn(policy, ctx)

        assert "http://localhost:8000/conversation/live/sess-abc" in result
        assert "Hello world" in result

    @pytest.mark.asyncio
    async def test_no_injection_on_subsequent_turn(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess-abc")

        result = await self._run_subsequent_turn(policy, ctx)

        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_no_injection_without_session_id(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id=None)

        result = await self._run_first_turn(policy, ctx)

        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_independent_sessions_both_get_link(self):
        """Each first-turn call gets a link — no cross-session state."""
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx1 = self._make_context(session_id="sess-1")
        ctx2 = self._make_context(session_id="sess-2")

        r1 = await self._run_first_turn(policy, ctx1)
        r2 = await self._run_first_turn(policy, ctx2)

        assert "/conversation/live/sess-1" in r1
        assert "/conversation/live/sess-2" in r2

    @pytest.mark.asyncio
    async def test_session_id_with_special_chars_is_url_encoded(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess with spaces#fragment")

        result = await self._run_first_turn(policy, ctx)

        assert "sess%20with%20spaces%23fragment" in result
        assert "sess with spaces" not in result

    @pytest.mark.asyncio
    async def test_multi_text_block_only_first_gets_link(self):
        """When a response has multiple text blocks, only the first is injected."""
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess-multi")
        await policy.on_anthropic_request(_first_turn_request(), ctx)

        # Call twice within same request (simulates two text blocks)
        r1 = await policy.simple_on_response_content("First block", ctx)
        r2 = await policy.simple_on_response_content("Second block", ctx)

        assert "conversation/live/sess-multi" in r1
        assert "conversation/live/" not in r2
        assert r2 == "Second block"

    @pytest.mark.asyncio
    async def test_request_passes_through(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context()

        result = await policy.simple_on_request("user message", ctx)

        assert result == "user message"

    def test_freeze_configured_state_passes(self):
        """Policy must pass the singleton state validation."""
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        policy.freeze_configured_state()

    def test_get_config_returns_base_url(self):
        """Config should be visible via get_config() for admin API."""
        policy = ConversationLinkPolicy(base_url="http://example.com:9000")

        config = policy.get_config()

        assert config["base_url"] == "http://example.com:9000"


class TestConversationLinkPolicyIntegration:
    """Test through the real SimplePolicy entry point (on_anthropic_response)."""

    def _make_context(self, session_id: str = "test-session") -> PolicyContext:
        return PolicyContext.for_testing(session_id=session_id)

    @pytest.mark.asyncio
    async def test_on_anthropic_response_injects_into_first_text_block(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess-resp")
        request = _first_turn_request()
        response = {
            "content": [
                {"type": "text", "text": "Hello from the assistant"},
            ],
        }

        await policy.on_anthropic_request(request, ctx)
        result = await policy.on_anthropic_response(response, ctx)

        text = result["content"][0]["text"]
        assert "conversation/live/sess-resp" in text
        assert "Hello from the assistant" in text

    @pytest.mark.asyncio
    async def test_tool_use_blocks_pass_through(self):
        """Tool use blocks are not affected by the link injection."""
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess-tool")
        request = _first_turn_request()
        response = {
            "content": [
                {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {"path": "/tmp"}},
                {"type": "text", "text": "Result here"},
            ],
        }

        await policy.on_anthropic_request(request, ctx)
        result = await policy.on_anthropic_response(response, ctx)

        tool_block = result["content"][0]
        assert tool_block["type"] == "tool_use"
        assert tool_block["input"] == {"path": "/tmp"}
        text = result["content"][1]["text"]
        assert "conversation/live/sess-tool" in text

    @pytest.mark.asyncio
    async def test_no_injection_on_subsequent_turn_via_response(self):
        """Full integration: subsequent turn through on_anthropic_response."""
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess-nope")
        request = _subsequent_turn_request()
        response = {
            "content": [
                {"type": "text", "text": "Just a normal response"},
            ],
        }

        await policy.on_anthropic_request(request, ctx)
        result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["text"] == "Just a normal response"
