"""Tests for ConversationLinkPolicy."""

import pytest

from luthien_proxy.policies.conversation_link_policy import (
    _MAX_TRACKED_SESSIONS,
    ConversationLinkPolicy,
    _injected_sessions,
    _mark_session_injected,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture(autouse=True)
def _clear_injected_sessions():
    """Clear the module-level tracking between tests."""
    _injected_sessions.clear()
    yield
    _injected_sessions.clear()


class TestConversationLinkPolicy:
    def _make_context(self, session_id: str | None = "test-session") -> PolicyContext:
        return PolicyContext.for_testing(session_id=session_id)

    @pytest.mark.asyncio
    async def test_injects_link_on_first_response(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id="sess-abc")

        result = await policy.simple_on_response_content("Hello world", ctx)

        assert "http://localhost:8000/conversation/live/sess-abc" in result
        assert "Hello world" in result

    @pytest.mark.asyncio
    async def test_does_not_inject_on_second_response(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx1 = self._make_context(session_id="sess-abc")
        ctx2 = self._make_context(session_id="sess-abc")

        await policy.simple_on_response_content("First", ctx1)
        result = await policy.simple_on_response_content("Second", ctx2)

        assert result == "Second"

    @pytest.mark.asyncio
    async def test_no_injection_without_session_id(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context(session_id=None)

        result = await policy.simple_on_response_content("Hello", ctx)

        assert result == "Hello"

    @pytest.mark.asyncio
    async def test_different_sessions_both_get_link(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx1 = self._make_context(session_id="sess-1")
        ctx2 = self._make_context(session_id="sess-2")

        r1 = await policy.simple_on_response_content("Hi", ctx1)
        r2 = await policy.simple_on_response_content("Hi", ctx2)

        assert "/conversation/live/sess-1" in r1
        assert "/conversation/live/sess-2" in r2

    @pytest.mark.asyncio
    async def test_request_passes_through(self):
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        ctx = self._make_context()

        result = await policy.simple_on_request("user message", ctx)

        assert result == "user message"

    def test_freeze_configured_state_passes(self):
        """Policy must pass the singleton state validation."""
        policy = ConversationLinkPolicy(base_url="http://localhost:8000")
        policy.freeze_configured_state()  # Should not raise

    def test_get_config_returns_base_url(self):
        """Config should be visible via get_config() for admin API."""
        policy = ConversationLinkPolicy(base_url="http://example.com:9000")

        config = policy.get_config()

        assert config["base_url"] == "http://example.com:9000"


class TestInjectedSessionsBounding:
    def test_evicts_oldest_at_capacity(self):
        for i in range(_MAX_TRACKED_SESSIONS + 5):
            _mark_session_injected(f"sess-{i}")

        assert len(_injected_sessions) == _MAX_TRACKED_SESSIONS
        # Oldest entries evicted
        assert "sess-0" not in _injected_sessions
        assert "sess-4" not in _injected_sessions
        # Newest entries present
        assert f"sess-{_MAX_TRACKED_SESSIONS + 4}" in _injected_sessions
