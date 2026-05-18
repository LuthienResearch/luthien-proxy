"""Unit tests for simple_llm_utils module.

Tests for the utility functions used by SimpleLLMPolicy:
- Judge prompt building
- Judge action parsing (pass/replace with text and tool blocks)
- Judge LLM calling with configuration
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    SimpleLLMJudgeConfig,
    build_judge_prompt,
    call_simple_llm_judge,
    parse_judge_action,
)


def _make_credential(value: str = "test-api-key") -> Credential:
    return Credential(value=value, credential_type=CredentialType.API_KEY)


def _make_config(**overrides) -> SimpleLLMJudgeConfig:
    """Build a config with the now-required auth_provider already set."""
    overrides.setdefault("instructions", "Be safe")
    overrides.setdefault("auth_provider", "user_credentials")
    return SimpleLLMJudgeConfig(**overrides)


class TestSimpleLLMJudgeConfig:
    """Test config model validation and defaults."""

    def test_defaults(self):
        config = _make_config()
        assert config.model == "claude-haiku-4-5"
        assert config.api_base is None
        assert config.temperature == 0.0
        assert config.max_tokens == 4096
        assert config.on_error == "pass"
        assert config.max_retries == 2
        assert config.retry_delay == 0.5

    def test_auth_provider_required(self):
        """Building a config without auth_provider now raises a validation error."""
        with pytest.raises(Exception):
            SimpleLLMJudgeConfig(instructions="Be safe")  # type: ignore[call-arg]

    def test_frozen(self):
        config = _make_config()
        with pytest.raises(Exception):
            config.model = "other"  # type: ignore[misc]

    def test_on_error_validation(self):
        _make_config(on_error="pass")
        _make_config(on_error="block")
        with pytest.raises(Exception):
            _make_config(on_error="ignore")


class TestBlockDescriptor:
    def test_text_block(self):
        b = BlockDescriptor(type="text", content="hello")
        assert b.type == "text"
        assert b.content == "hello"

    def test_tool_block(self):
        b = BlockDescriptor(type="tool_use", content='{"name":"foo","input":{}}')
        assert b.type == "tool_use"

    def test_frozen(self):
        b = BlockDescriptor(type="text", content="hello")
        with pytest.raises(Exception):
            b.type = "tool_use"  # type: ignore[misc]


class TestParseJudgeAction:
    """Test parsing judge responses into JudgeAction."""

    def test_pass_action(self):
        raw = '{"action": "pass"}'
        result = parse_judge_action(raw)
        assert result.action == "pass"
        assert result.blocks is None

    def test_replace_with_text_block(self):
        raw = json.dumps(
            {
                "action": "replace",
                "blocks": [{"type": "text", "text": "safe response"}],
            }
        )
        result = parse_judge_action(raw)
        assert result.action == "replace"
        assert result.blocks is not None
        assert len(result.blocks) == 1
        assert result.blocks[0].type == "text"
        assert result.blocks[0].text == "safe response"

    def test_replace_with_tool_block(self):
        raw = json.dumps(
            {
                "action": "replace",
                "blocks": [
                    {
                        "type": "tool_use",
                        "name": "safe_tool",
                        "input": {"key": "value"},
                    }
                ],
            }
        )
        result = parse_judge_action(raw)
        assert result.action == "replace"
        assert result.blocks is not None
        assert result.blocks[0].type == "tool_use"
        assert result.blocks[0].name == "safe_tool"
        assert result.blocks[0].input == {"key": "value"}

    def test_replace_with_multiple_blocks(self):
        raw = json.dumps(
            {
                "action": "replace",
                "blocks": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "second"},
                ],
            }
        )
        result = parse_judge_action(raw)
        assert result.blocks is not None
        assert len(result.blocks) == 2

    def test_fenced_json(self):
        raw = '```json\n{"action": "pass"}\n```'
        result = parse_judge_action(raw)
        assert result.action == "pass"

    def test_invalid_action(self):
        raw = '{"action": "block"}'
        with pytest.raises(ValueError, match="action must be"):
            parse_judge_action(raw)

    def test_missing_action(self):
        raw = '{"blocks": []}'
        with pytest.raises(ValueError, match="missing.*action"):
            parse_judge_action(raw)

    def test_replace_missing_blocks(self):
        raw = '{"action": "replace"}'
        with pytest.raises(ValueError, match="non-empty.*blocks"):
            parse_judge_action(raw)

    def test_replace_empty_blocks(self):
        raw = '{"action": "replace", "blocks": []}'
        with pytest.raises(ValueError, match="non-empty.*blocks"):
            parse_judge_action(raw)

    def test_text_block_missing_text(self):
        raw = json.dumps(
            {
                "action": "replace",
                "blocks": [{"type": "text"}],
            }
        )
        with pytest.raises(ValueError, match="text.*required"):
            parse_judge_action(raw)

    def test_tool_block_missing_name(self):
        raw = json.dumps(
            {
                "action": "replace",
                "blocks": [{"type": "tool_use", "input": {}}],
            }
        )
        with pytest.raises(ValueError, match="name.*required"):
            parse_judge_action(raw)

    def test_block_missing_type(self):
        raw = json.dumps(
            {
                "action": "replace",
                "blocks": [{"text": "hello"}],
            }
        )
        with pytest.raises(ValueError, match="type"):
            parse_judge_action(raw)

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="JSON"):
            parse_judge_action("not json")


class TestBuildJudgePrompt:
    """Test prompt construction."""

    def test_basic_structure(self):
        prompt = build_judge_prompt(
            instructions="Be safe",
            current_block=BlockDescriptor(type="text", content="hello"),
            previous_blocks=(),
        )
        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert prompt[1]["role"] == "user"

    def test_instructions_in_system(self):
        prompt = build_judge_prompt(
            instructions="Block harmful content",
            current_block=BlockDescriptor(type="text", content="test"),
            previous_blocks=(),
        )
        assert "Block harmful content" in prompt[0]["content"]

    def test_json_schema_in_system(self):
        prompt = build_judge_prompt(
            instructions="Be safe",
            current_block=BlockDescriptor(type="text", content="test"),
            previous_blocks=(),
        )
        system = prompt[0]["content"]
        assert "pass" in system
        assert "replace" in system
        assert "blocks" in system

    def test_current_block_in_user(self):
        prompt = build_judge_prompt(
            instructions="Be safe",
            current_block=BlockDescriptor(type="text", content="the actual content"),
            previous_blocks=(),
        )
        assert "the actual content" in prompt[1]["content"]

    def test_previous_blocks_included(self):
        prompt = build_judge_prompt(
            instructions="Be safe",
            current_block=BlockDescriptor(type="text", content="current"),
            previous_blocks=(BlockDescriptor(type="text", content="previous stuff"),),
        )
        user_msg = prompt[1]["content"]
        assert "previous stuff" in user_msg
        assert "current" in user_msg


class TestCallSimpleLLMJudge:
    """Tests for call_simple_llm_judge via the credential-based judge path.

    The function mocks luthien_proxy.policies.simple_llm_utils.judge_completion
    (the credential-aware wrapper around LiteLLM acompletion).
    """

    @pytest.mark.asyncio
    async def test_pass_result(self):
        config = _make_config(model="test-model")

        with patch(
            "luthien_proxy.policies.simple_llm_utils.judge_completion",
            new=AsyncMock(return_value='{"action": "pass"}'),
        ):
            result = await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
                credential=_make_credential(),
            )

        assert result.action == "pass"

    @pytest.mark.asyncio
    async def test_replace_result(self):
        config = _make_config(model="test-model")

        response_data = json.dumps(
            {
                "action": "replace",
                "blocks": [{"type": "text", "text": "sanitized"}],
            }
        )

        with patch(
            "luthien_proxy.policies.simple_llm_utils.judge_completion",
            new=AsyncMock(return_value=response_data),
        ):
            result = await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="bad"),
                previous_blocks=(),
                credential=_make_credential(),
            )

        assert result.action == "replace"
        assert result.blocks is not None
        assert result.blocks[0].text == "sanitized"

    @pytest.mark.asyncio
    async def test_uses_json_response_format(self):
        """judge_completion is called with response_format={'type': 'json_object'}."""
        config = _make_config(model="test-model")
        mock_jc = AsyncMock(return_value='{"action": "pass"}')

        with patch("luthien_proxy.policies.simple_llm_utils.judge_completion", new=mock_jc):
            await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
                credential=_make_credential(),
            )

        call_kwargs = mock_jc.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_forwards_credential_and_model_args(self):
        config = _make_config(model="test-model", api_base="http://localhost:8080")
        mock_jc = AsyncMock(return_value='{"action": "pass"}')
        credential = _make_credential("my-key")

        with patch("luthien_proxy.policies.simple_llm_utils.judge_completion", new=mock_jc):
            await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
                credential=credential,
            )

        args, kwargs = mock_jc.call_args
        assert args[0] is credential
        assert kwargs["model"] == "test-model"
        assert kwargs["api_base"] == "http://localhost:8080"

    @pytest.mark.asyncio
    async def test_error_propagation_after_retries(self):
        """All attempts fail → raises the last exception."""
        config = _make_config(model="test-model", max_retries=2, retry_delay=0)

        mock_jc = AsyncMock(side_effect=RuntimeError("LLM failed"))
        with patch("luthien_proxy.policies.simple_llm_utils.judge_completion", new=mock_jc):
            with pytest.raises(RuntimeError, match="LLM failed"):
                await call_simple_llm_judge(
                    config=config,
                    current_block=BlockDescriptor(type="text", content="hello"),
                    previous_blocks=(),
                    credential=_make_credential(),
                )
            assert mock_jc.call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_no_retries_when_max_retries_zero(self):
        config = _make_config(model="test-model", max_retries=0, retry_delay=0)

        mock_jc = AsyncMock(side_effect=RuntimeError("LLM failed"))
        with patch("luthien_proxy.policies.simple_llm_utils.judge_completion", new=mock_jc):
            with pytest.raises(RuntimeError, match="LLM failed"):
                await call_simple_llm_judge(
                    config=config,
                    current_block=BlockDescriptor(type="text", content="hello"),
                    previous_blocks=(),
                    credential=_make_credential(),
                )
            assert mock_jc.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        config = _make_config(model="test-model", max_retries=2, retry_delay=0)

        mock_jc = AsyncMock(side_effect=[RuntimeError("transient"), '{"action": "pass"}'])
        with patch("luthien_proxy.policies.simple_llm_utils.judge_completion", new=mock_jc):
            result = await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
                credential=_make_credential(),
            )

        assert result.action == "pass"
        assert mock_jc.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_delay_is_applied(self):
        config = _make_config(model="test-model", max_retries=1, retry_delay=0.5)

        mock_jc = AsyncMock(side_effect=[RuntimeError("transient"), '{"action": "pass"}'])
        with (
            patch("luthien_proxy.policies.simple_llm_utils.judge_completion", new=mock_jc),
            patch(
                "luthien_proxy.policies.simple_llm_utils.asyncio.sleep",
                return_value=None,
            ) as mock_sleep,
        ):
            result = await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
                credential=_make_credential(),
            )

        assert result.action == "pass"
        mock_sleep.assert_called_once_with(0.5)

    @pytest.mark.asyncio
    async def test_retry_on_parse_failure(self):
        """Parse failure on first attempt, valid response on retry."""
        config = _make_config(model="test-model", max_retries=1, retry_delay=0)

        mock_jc = AsyncMock(side_effect=["not valid json", '{"action": "pass"}'])
        with patch("luthien_proxy.policies.simple_llm_utils.judge_completion", new=mock_jc):
            result = await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
                credential=_make_credential(),
            )

        assert result.action == "pass"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
