"""Unit tests for simple_llm_utils module.

Tests for the utility functions used by SimpleLLMPolicy:
- Judge prompt building
- Judge action parsing (pass/replace with text and tool blocks)
- Judge LLM calling with configuration
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    SimpleLLMJudgeConfig,
    build_judge_prompt,
    call_simple_llm_judge,
    parse_judge_action,
)


class TestSimpleLLMJudgeConfig:
    """Test config model validation and defaults."""

    def test_defaults(self):
        config = SimpleLLMJudgeConfig(instructions="Be safe")
        assert config.model == "claude-haiku-4-5"
        assert config.api_base is None
        assert config.api_key is None
        assert config.temperature == 0.0
        assert config.max_tokens == 4096
        assert config.on_error == "pass"

    def test_frozen(self):
        config = SimpleLLMJudgeConfig(instructions="Be safe")
        with pytest.raises(Exception):
            config.model = "other"  # type: ignore[misc]

    def test_on_error_validation(self):
        SimpleLLMJudgeConfig(instructions="x", on_error="pass")
        SimpleLLMJudgeConfig(instructions="x", on_error="block")
        with pytest.raises(Exception):
            SimpleLLMJudgeConfig(instructions="x", on_error="ignore")


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
    """Test the judge calling function."""

    @pytest.mark.asyncio
    async def test_pass_result(self):
        config = SimpleLLMJudgeConfig(
            instructions="Be safe",
            model="test-model",
        )

        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"action": "pass"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch(
            "luthien_proxy.policies.simple_llm_utils.acompletion",
            return_value=mock_response,
        ):
            result = await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
            )

        assert result.action == "pass"

    @pytest.mark.asyncio
    async def test_replace_result(self):
        config = SimpleLLMJudgeConfig(
            instructions="Be safe",
            model="test-model",
        )

        response_data = json.dumps(
            {
                "action": "replace",
                "blocks": [{"type": "text", "text": "sanitized"}],
            }
        )

        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_data,
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch(
            "luthien_proxy.policies.simple_llm_utils.acompletion",
            return_value=mock_response,
        ):
            result = await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="bad"),
                previous_blocks=(),
            )

        assert result.action == "replace"
        assert result.blocks is not None
        assert result.blocks[0].text == "sanitized"

    @pytest.mark.asyncio
    async def test_uses_json_response_format(self):
        config = SimpleLLMJudgeConfig(
            instructions="Be safe",
            model="test-model",
        )

        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"action": "pass"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch(
            "luthien_proxy.policies.simple_llm_utils.acompletion",
            return_value=mock_response,
        ) as mock_acompletion:
            await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_uses_config_api_key(self):
        config = SimpleLLMJudgeConfig(
            instructions="Be safe",
            model="test-model",
            api_key="my-key",
        )

        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"action": "pass"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch(
            "luthien_proxy.policies.simple_llm_utils.acompletion",
            return_value=mock_response,
        ) as mock_acompletion:
            await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["api_key"] == "my-key"

    @pytest.mark.asyncio
    async def test_falls_back_to_settings_key(self):
        config = SimpleLLMJudgeConfig(
            instructions="Be safe",
            model="test-model",
        )

        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"action": "pass"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        mock_settings = type("Settings", (), {"litellm_master_key": "master-key"})()

        with (
            patch(
                "luthien_proxy.policies.simple_llm_utils.acompletion",
                return_value=mock_response,
            ) as mock_acompletion,
            patch(
                "luthien_proxy.policies.simple_llm_utils.get_settings",
                return_value=mock_settings,
            ),
        ):
            await call_simple_llm_judge(
                config=config,
                current_block=BlockDescriptor(type="text", content="hello"),
                previous_blocks=(),
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["api_key"] == "master-key"

    @pytest.mark.asyncio
    async def test_error_propagation(self):
        config = SimpleLLMJudgeConfig(
            instructions="Be safe",
            model="test-model",
        )

        with patch(
            "luthien_proxy.policies.simple_llm_utils.acompletion",
            side_effect=RuntimeError("LLM failed"),
        ):
            with pytest.raises(RuntimeError, match="LLM failed"):
                await call_simple_llm_judge(
                    config=config,
                    current_block=BlockDescriptor(type="text", content="hello"),
                    previous_blocks=(),
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
