"""Unit tests for ClaudeMdRulesPolicy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policies.claude_md_rules_policy import (
    ClaudeMdRulesConfig,
    ClaudeMdRulesPolicy,
)
from luthien_proxy.policy_core import AnthropicExecutionInterface, BasePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.storage.session_rules import SessionRule


def _make_litellm_response(content: str) -> ModelResponse:
    return ModelResponse(
        id="test-id",
        choices=[Choices(finish_reason="stop", index=0, message=Message(content=content, role="assistant"))],
        created=1234567890,
        model="test-model",
        object="chat.completion",
    )


class _StubIO:
    def __init__(self, request: dict | None = None, response_text: str = "Hello from assistant"):
        self._request = request or {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 100,
            "stream": False,
        }
        self._response_text = response_text

    @property
    def request(self) -> dict:
        return self._request

    def set_request(self, request: dict) -> None:
        self._request = request

    @property
    def first_backend_response(self) -> AnthropicResponse | None:
        return None

    async def complete(self, request: dict | None = None) -> AnthropicResponse:
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": self._response_text}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    def stream(self, request: dict | None = None) -> AsyncIterator:
        async def _gen():
            return
            yield
        return _gen()


class TestClaudeMdRulesPolicyProtocol:
    def test_inherits_base_policy(self):
        policy = ClaudeMdRulesPolicy()
        assert isinstance(policy, BasePolicy)

    def test_implements_anthropic_interface(self):
        policy = ClaudeMdRulesPolicy()
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_short_policy_name(self):
        policy = ClaudeMdRulesPolicy()
        assert policy.short_policy_name == "ClaudeMdRules"


class TestClaudeMdContentDetection:
    """Test _find_claude_md_content for various request shapes."""

    def test_finds_claude_md_in_system_string(self):
        policy = ClaudeMdRulesPolicy()
        request = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [],
            "max_tokens": 100,
            "system": "Contents of CLAUDE.md:\n## Style\n- Be concise",
        }
        result = policy._find_claude_md_content(request)
        assert result is not None
        assert "CLAUDE.md" in result

    def test_finds_claude_md_in_system_blocks(self):
        policy = ClaudeMdRulesPolicy()
        request = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [],
            "max_tokens": 100,
            "system": [{"type": "text", "text": "From CLAUDE.md: ## Rules\n- No emojis"}],
        }
        result = policy._find_claude_md_content(request)
        assert result is not None

    def test_finds_claude_md_in_first_message(self):
        policy = ClaudeMdRulesPolicy()
        request = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "CLAUDE.md says ## Style be terse"}],
            "max_tokens": 100,
        }
        result = policy._find_claude_md_content(request)
        assert result is not None

    def test_returns_none_without_indicators(self):
        policy = ClaudeMdRulesPolicy()
        request = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Just a normal message"}],
            "max_tokens": 100,
        }
        result = policy._find_claude_md_content(request)
        assert result is None

    def test_returns_none_for_empty_request(self):
        policy = ClaudeMdRulesPolicy()
        request = {"model": DEFAULT_TEST_MODEL, "messages": [], "max_tokens": 100}
        result = policy._find_claude_md_content(request)
        assert result is None


class TestRuleExtractionParsing:
    """Test _parse_extracted_rules for various LLM outputs."""

    def test_parses_json_array(self):
        policy = ClaudeMdRulesPolicy()
        output = '[{"name": "concise", "instruction": "Be concise"}]'
        rules = policy._parse_extracted_rules(output)
        assert len(rules) == 1
        assert rules[0].name == "concise"

    def test_parses_fenced_json(self):
        policy = ClaudeMdRulesPolicy()
        output = '```json\n[{"name": "test", "instruction": "Test rule"}]\n```'
        rules = policy._parse_extracted_rules(output)
        assert len(rules) == 1

    def test_returns_empty_for_invalid_json(self):
        policy = ClaudeMdRulesPolicy()
        rules = policy._parse_extracted_rules("not json at all")
        assert rules == []

    def test_returns_empty_for_non_array(self):
        policy = ClaudeMdRulesPolicy()
        rules = policy._parse_extracted_rules('{"name": "test"}')
        assert rules == []

    def test_skips_malformed_entries(self):
        policy = ClaudeMdRulesPolicy()
        output = '[{"name": "good", "instruction": "ok"}, {"bad": true}]'
        rules = policy._parse_extracted_rules(output)
        assert len(rules) == 1
        assert rules[0].name == "good"

    def test_empty_array(self):
        policy = ClaudeMdRulesPolicy()
        rules = policy._parse_extracted_rules("[]")
        assert rules == []


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_no_session_id_passes_through(self):
        """Without session_id, policy passes through without modification."""
        policy = ClaudeMdRulesPolicy()
        io = _StubIO()
        ctx = PolicyContext.for_testing(session_id=None)

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        assert emissions[0]["content"][0]["text"] == "Hello from assistant"

    @pytest.mark.asyncio
    async def test_no_db_pool_passes_through(self):
        """Without db_pool, policy passes through without modification."""
        policy = ClaudeMdRulesPolicy()
        io = _StubIO()
        ctx = PolicyContext.for_testing(session_id="session-123", db_pool=None)

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        assert emissions[0]["content"][0]["text"] == "Hello from assistant"


class TestFirstTurnExtraction:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.claude_md_rules_policy.has_rules", new_callable=AsyncMock, return_value=False)
    @patch("luthien_proxy.policies.claude_md_rules_policy.save_rules", new_callable=AsyncMock)
    @patch("luthien_proxy.policies.claude_md_rules_policy.acompletion")
    @patch("luthien_proxy.policies.parallel_rules_policy.acompletion")
    async def test_extracts_and_saves_rules_on_first_turn(
        self, mock_parallel_acompletion, mock_extraction_acompletion, mock_save, mock_has_rules
    ):
        """First turn: extract rules from CLAUDE.md, save to DB, apply."""
        # Extraction LLM returns rules
        mock_extraction_acompletion.return_value = _make_litellm_response(
            '[{"name": "concise", "instruction": "Be concise"}]'
        )
        # Parallel rules application
        mock_parallel_acompletion.return_value = _make_litellm_response("Concise response")

        policy = ClaudeMdRulesPolicy()
        request = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 100,
            "stream": False,
            "system": "CLAUDE.md instructions: ## Style\n- Be concise",
        }
        io = _StubIO(request=request)
        mock_db_pool = MagicMock()
        ctx = PolicyContext.for_testing(session_id="session-123", db_pool=mock_db_pool)

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        # Verify save_rules was called
        mock_save.assert_called_once()
        saved_rules = mock_save.call_args[0][2]
        assert len(saved_rules) == 1
        assert saved_rules[0].name == "concise"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.claude_md_rules_policy.has_rules", new_callable=AsyncMock, return_value=False)
    @patch("luthien_proxy.policies.claude_md_rules_policy.save_rules", new_callable=AsyncMock)
    async def test_no_claude_md_saves_empty_rules(self, mock_save, mock_has_rules):
        """First turn without CLAUDE.md: saves empty rules to avoid re-scanning."""
        policy = ClaudeMdRulesPolicy()
        io = _StubIO()  # No CLAUDE.md content in default request
        mock_db_pool = MagicMock()
        ctx = PolicyContext.for_testing(session_id="session-123", db_pool=mock_db_pool)

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        mock_save.assert_called_once()
        saved_rules = mock_save.call_args[0][2]
        assert saved_rules == []


class TestSubsequentTurnLoading:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.claude_md_rules_policy.has_rules", new_callable=AsyncMock, return_value=True)
    @patch(
        "luthien_proxy.policies.claude_md_rules_policy.load_rules",
        new_callable=AsyncMock,
        return_value=[SessionRule(name="concise", instruction="Be concise")],
    )
    @patch("luthien_proxy.policies.parallel_rules_policy.acompletion")
    async def test_loads_rules_from_db(self, mock_acompletion, mock_load, mock_has_rules):
        """Subsequent turns load rules from DB and apply them."""
        mock_acompletion.return_value = _make_litellm_response("Concise response")

        policy = ClaudeMdRulesPolicy()
        io = _StubIO()
        mock_db_pool = MagicMock()
        ctx = PolicyContext.for_testing(session_id="session-123", db_pool=mock_db_pool)

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        mock_load.assert_called_once()
        # The parallel rules policy should have been called to apply the loaded rule
        assert mock_acompletion.call_count >= 1
