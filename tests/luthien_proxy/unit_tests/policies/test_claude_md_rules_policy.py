"""Unit tests for ClaudeMdRulesPolicy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policies.claude_md_rules_policy import (
    ClaudeMdRulesPolicy,
    _find_claude_md_content,
    _parse_extracted_rules,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.storage.session_rules import SessionRule


def _make_litellm_response(content: str) -> ModelResponse:
    return ModelResponse(
        id="test-id",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=Message(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model="test-model",
        object="chat.completion",
    )


def _make_stub_io(request: AnthropicRequest | None = None) -> MagicMock:
    """Create a stub AnthropicPolicyIOProtocol."""
    io = MagicMock()
    io.request = request or {
        "model": DEFAULT_TEST_MODEL,
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 100,
    }
    io.first_backend_response = None

    response: AnthropicResponse = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Original text"}],
        "model": DEFAULT_TEST_MODEL,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    async def mock_complete(req=None):
        return response

    io.complete = mock_complete
    return io


class TestFindClaudeMdContent:
    def test_finds_in_system_string(self):
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "system": "Contents of CLAUDE.md: Be concise.",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        result = _find_claude_md_content(request)
        assert result is not None
        assert "CLAUDE.md" in result

    def test_finds_in_system_blocks(self):
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "system": [{"type": "text", "text": "File: CLAUDE.md\nPrefer early returns."}],
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        result = _find_claude_md_content(request)
        assert result is not None
        assert "early returns" in result

    def test_finds_in_first_user_message(self):
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [
                {"role": "user", "content": "Here is my CLAUDE.md: no jargon."},
            ],
            "max_tokens": 100,
        }
        result = _find_claude_md_content(request)
        assert result is not None

    def test_returns_none_when_no_indicators(self):
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Just a normal message."}],
            "max_tokens": 100,
        }
        result = _find_claude_md_content(request)
        assert result is None

    def test_finds_system_reminder_tag(self):
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "system": "<system-reminder>Be helpful</system-reminder>",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        result = _find_claude_md_content(request)
        assert result is not None

    def test_returns_none_for_empty_messages(self):
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [],
            "max_tokens": 100,
        }
        result = _find_claude_md_content(request)
        assert result is None

    def test_finds_claudeMd_indicator(self):
        """Detect the 'claudeMd' indicator used by Claude Code."""
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "system": "# claudeMd\nPrefer f-strings.",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        result = _find_claude_md_content(request)
        assert result is not None


class TestParseExtractedRules:
    def test_parses_valid_json_array(self):
        output = '[{"name": "no-emoji", "instruction": "Never use emoji."}]'
        rules = _parse_extracted_rules(output)
        assert len(rules) == 1
        assert rules[0].name == "no-emoji"
        assert rules[0].instruction == "Never use emoji."

    def test_parses_multiple_rules(self):
        output = """[
            {"name": "concise", "instruction": "Be concise."},
            {"name": "no-jargon", "instruction": "Avoid jargon."}
        ]"""
        rules = _parse_extracted_rules(output)
        assert len(rules) == 2

    def test_handles_empty_array(self):
        rules = _parse_extracted_rules("[]")
        assert rules == []

    def test_strips_markdown_fences(self):
        output = '```json\n[{"name": "r1", "instruction": "Do it."}]\n```'
        rules = _parse_extracted_rules(output)
        assert len(rules) == 1
        assert rules[0].name == "r1"

    def test_handles_leading_text(self):
        output = 'Here are the rules:\n[{"name": "r1", "instruction": "Do it."}]'
        rules = _parse_extracted_rules(output)
        assert len(rules) == 1

    def test_returns_empty_for_no_json(self):
        rules = _parse_extracted_rules("I found no rules to extract.")
        assert rules == []

    def test_skips_malformed_items(self):
        output = '[{"name": "ok", "instruction": "Good"}, {"bad": true}, {"name": "", "instruction": "empty name"}]'
        rules = _parse_extracted_rules(output)
        assert len(rules) == 1
        assert rules[0].name == "ok"

    def test_returns_empty_for_invalid_json(self):
        rules = _parse_extracted_rules("[{invalid json}]")
        assert rules == []


class TestClaudeMdRulesPolicyPassthrough:
    """Tests for graceful degradation when session_id or db_pool is missing."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_no_session_id_passthrough(self, mock_acompletion):
        """Without session_id, passes through to ParallelRulesPolicy with no rules."""
        mock_acompletion.return_value = _make_litellm_response("unused")

        policy = ClaudeMdRulesPolicy()
        ctx = PolicyContext.for_testing()  # no session_id
        io = _make_stub_io()

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        # Should pass through without calling extraction LLM
        mock_acompletion.assert_not_called()

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_no_db_pool_passthrough(self, mock_acompletion):
        """Without db_pool, passes through with no rules."""
        mock_acompletion.return_value = _make_litellm_response("unused")

        policy = ClaudeMdRulesPolicy()
        ctx = PolicyContext.for_testing(session_id="sess-1")  # no db_pool
        io = _make_stub_io()

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        mock_acompletion.assert_not_called()


class TestClaudeMdRulesPolicyExtraction:
    """Tests for first-turn rule extraction and subsequent-turn loading."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.claude_md_rules_policy.save_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.load_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.has_rules")
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_first_turn_extracts_and_saves(
        self, mock_acompletion, mock_has_rules, mock_load_rules, mock_save_rules
    ):
        """On first turn with CLAUDE.md, extracts rules and saves to DB."""
        mock_has_rules.return_value = False
        mock_save_rules.return_value = None

        # Extraction LLM returns rules
        extraction_response = _make_litellm_response('[{"name": "concise", "instruction": "Be concise."}]')
        # Rule application LLM
        application_response = _make_litellm_response("Concise text")

        mock_acompletion.side_effect = [extraction_response, application_response]

        policy = ClaudeMdRulesPolicy()
        mock_db = MagicMock()
        ctx = PolicyContext.for_testing(session_id="sess-1", db_pool=mock_db)
        io = _make_stub_io(
            request={
                "model": DEFAULT_TEST_MODEL,
                "system": "Contents of CLAUDE.md: Be concise always.",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 100,
            }
        )

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        # Extraction call + 1 rule application call
        assert mock_acompletion.call_count == 2
        # Rules were saved
        mock_save_rules.assert_called_once()
        saved_rules = mock_save_rules.call_args.args[2]
        assert len(saved_rules) == 1
        assert saved_rules[0].name == "concise"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.claude_md_rules_policy.save_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.load_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.has_rules")
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_subsequent_turn_loads_from_db(
        self, mock_acompletion, mock_has_rules, mock_load_rules, mock_save_rules
    ):
        """On subsequent turns, loads rules from DB without extraction."""
        mock_has_rules.return_value = True
        mock_load_rules.return_value = [SessionRule(name="concise", instruction="Be concise.")]

        mock_acompletion.return_value = _make_litellm_response("Concise text")

        policy = ClaudeMdRulesPolicy()
        mock_db = MagicMock()
        ctx = PolicyContext.for_testing(session_id="sess-1", db_pool=mock_db)
        io = _make_stub_io()

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        # Only rule application, no extraction
        assert mock_acompletion.call_count == 1
        mock_save_rules.assert_not_called()

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.claude_md_rules_policy.save_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.load_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.has_rules")
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_no_claude_md_saves_empty_sentinel(
        self, mock_acompletion, mock_has_rules, mock_load_rules, mock_save_rules
    ):
        """When no CLAUDE.md found, saves empty rules (sentinel) to avoid re-scanning."""
        mock_has_rules.return_value = False
        mock_save_rules.return_value = None

        policy = ClaudeMdRulesPolicy()
        mock_db = MagicMock()
        ctx = PolicyContext.for_testing(session_id="sess-1", db_pool=mock_db)
        io = _make_stub_io()  # No CLAUDE.md in request

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        # Saved empty rules
        mock_save_rules.assert_called_once()
        saved_rules = mock_save_rules.call_args.args[2]
        assert saved_rules == []

        # No extraction LLM call (no CLAUDE.md content)
        mock_acompletion.assert_not_called()

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.claude_md_rules_policy.save_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.load_rules")
    @patch("luthien_proxy.policies.claude_md_rules_policy.has_rules")
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_extraction_failure_saves_sentinel(
        self, mock_acompletion, mock_has_rules, mock_load_rules, mock_save_rules
    ):
        """When extraction LLM fails, saves empty sentinel and continues."""
        mock_has_rules.return_value = False
        mock_save_rules.return_value = None

        # Extraction fails, then rule application succeeds (for passthrough)
        mock_acompletion.side_effect = [Exception("LLM error")]

        policy = ClaudeMdRulesPolicy()
        mock_db = MagicMock()
        ctx = PolicyContext.for_testing(session_id="sess-1", db_pool=mock_db)
        io = _make_stub_io(
            request={
                "model": DEFAULT_TEST_MODEL,
                "system": "Contents of CLAUDE.md: rules here",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 100,
            }
        )

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        # Should have saved empty rules on failure
        mock_save_rules.assert_called_once()
        saved_rules = mock_save_rules.call_args.args[2]
        assert saved_rules == []

        # Response still emitted (passthrough)
        assert len(emissions) == 1


class TestClaudeMdRulesPolicyConfig:
    def test_default_config(self):
        policy = ClaudeMdRulesPolicy()
        assert policy.config.model == "claude-haiku-4-5"
        assert policy.config.temperature == 0.0

    def test_custom_config(self):
        policy = ClaudeMdRulesPolicy(config={"model": "claude-opus", "temperature": 0.5})
        assert policy.config.model == "claude-opus"
        assert policy.config.temperature == 0.5

    def test_short_policy_name(self):
        policy = ClaudeMdRulesPolicy()
        assert policy.short_policy_name == "ClaudeMdRules"
