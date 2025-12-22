"""Unit tests for ParallelRulesPolicy.

Tests the policy's behavior including:
- Rule filtering by response type
- Parallel rule evaluation
- Violation aggregation
- Per-rule violation response formatting
- Streaming and non-streaming paths
- Error handling and fail-secure behavior
- Configuration parsing
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from litellm.types.utils import (
    ChatCompletionDeltaToolCall,
    Delta,
    Function,
    ModelResponse,
    StreamingChoices,
)

from luthien_proxy.llm.types import Request
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.parallel_rules_config import (
    ParallelRulesJudgeConfig,
    ResponseType,
    RuleConfig,
    RuleResult,
    RuleViolation,
    ViolationResponseConfig,
)
from luthien_proxy.policies.parallel_rules_policy import ParallelRulesPolicy
from luthien_proxy.policies.parallel_rules_utils import (
    build_rule_prompt,
    format_violation_message,
    parse_rule_response,
)
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock
from luthien_proxy.streaming.stream_state import StreamState


def create_mock_streaming_context(
    transaction_id: str = "test-call-id",
    just_completed=None,
    raw_chunks: list[ModelResponse] | None = None,
    finish_reason: str | None = None,
) -> StreamingPolicyContext:
    """Create a mock StreamingPolicyContext for testing."""
    ctx = Mock(spec=StreamingPolicyContext)

    ctx.policy_ctx = Mock(spec=PolicyContext)
    ctx.policy_ctx.transaction_id = transaction_id
    ctx.policy_ctx.request = Request(
        model="test-model",
        messages=[{"role": "user", "content": "test"}],
    )
    ctx.policy_ctx.scratchpad = {}
    ctx.policy_ctx.record_event = Mock()

    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.just_completed = just_completed
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []
    ctx.original_streaming_response_state.finish_reason = finish_reason

    ctx.egress_queue = Mock()
    ctx.egress_queue.put_nowait = Mock()
    ctx.egress_queue.put = AsyncMock()

    ctx.keepalive = Mock()

    return ctx


class TestParallelRulesConfig:
    """Test configuration parsing."""

    def test_response_type_from_string(self):
        """Test ResponseType.from_string conversion."""
        assert ResponseType.from_string("text") == ResponseType.TEXT
        assert ResponseType.from_string("TEXT") == ResponseType.TEXT
        assert ResponseType.from_string("tool_call") == ResponseType.TOOL_CALL
        assert ResponseType.from_string("other") == ResponseType.OTHER

    def test_response_type_invalid_raises(self):
        """Test that invalid response type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid response type"):
            ResponseType.from_string("invalid")

    def test_violation_response_config_defaults(self):
        """Test ViolationResponseConfig default values."""
        config = ViolationResponseConfig()
        assert config.include_original is False
        assert config.static_message is None
        assert config.include_llm_explanation is True
        assert "Rule '{rule_name}'" in config.llm_explanation_template

    def test_violation_response_config_from_dict(self):
        """Test ViolationResponseConfig.from_dict parsing."""
        data = {
            "include_original": True,
            "static_message": "Blocked!",
            "include_llm_explanation": False,
            "llm_explanation_template": "Custom: {explanation}",
        }
        config = ViolationResponseConfig.from_dict(data)
        assert config.include_original is True
        assert config.static_message == "Blocked!"
        assert config.include_llm_explanation is False
        assert config.llm_explanation_template == "Custom: {explanation}"

    def test_rule_config_from_dict_minimal(self):
        """Test RuleConfig.from_dict with minimal configuration."""
        data = {
            "name": "test_rule",
            "ruletext": "Is this response harmful?",
        }
        rule = RuleConfig.from_dict(data)
        assert rule.name == "test_rule"
        assert rule.ruletext == "Is this response harmful?"
        assert ResponseType.TEXT in rule.response_types
        assert rule.probability_threshold is None
        assert rule.judge_prompt_template is None

    def test_rule_config_from_dict_full(self):
        """Test RuleConfig.from_dict with full configuration."""
        data = {
            "name": "full_rule",
            "ruletext": "Check for profanity",
            "response_types": ["text", "tool_call"],
            "probability_threshold": 0.8,
            "judge_prompt_template": "Custom template: {ruletext}\n{content}",
            "violation_response": {
                "include_original": True,
                "static_message": "Profanity detected!",
            },
        }
        rule = RuleConfig.from_dict(data)
        assert rule.name == "full_rule"
        assert ResponseType.TEXT in rule.response_types
        assert ResponseType.TOOL_CALL in rule.response_types
        assert rule.probability_threshold == 0.8
        assert "Custom template" in rule.judge_prompt_template
        assert rule.violation_response.include_original is True

    def test_rule_config_missing_name_raises(self):
        """Test that missing name raises ValueError."""
        with pytest.raises(ValueError, match="must have a 'name'"):
            RuleConfig.from_dict({"ruletext": "test"})

    def test_rule_config_missing_ruletext_raises(self):
        """Test that missing ruletext raises ValueError."""
        with pytest.raises(ValueError, match="must have a 'ruletext'"):
            RuleConfig.from_dict({"name": "test"})

    def test_rule_config_get_threshold(self):
        """Test RuleConfig.get_threshold with and without override."""
        rule_with_override = RuleConfig(
            name="test",
            ruletext="test",
            response_types=frozenset([ResponseType.TEXT]),
            probability_threshold=0.9,
        )
        rule_without_override = RuleConfig(
            name="test",
            ruletext="test",
            response_types=frozenset([ResponseType.TEXT]),
        )

        assert rule_with_override.get_threshold(0.5) == 0.9
        assert rule_without_override.get_threshold(0.5) == 0.5

    def test_judge_config_from_dict(self):
        """Test ParallelRulesJudgeConfig.from_dict parsing."""
        data = {
            "model": "claude-3-haiku",
            "api_base": "http://localhost:8000",
            "temperature": 0.1,
            "max_tokens": 128,
            "probability_threshold": 0.7,
        }
        config = ParallelRulesJudgeConfig.from_dict(data)
        assert config.model == "claude-3-haiku"
        assert config.api_base == "http://localhost:8000"
        assert config.temperature == 0.1
        assert config.max_tokens == 128
        assert config.probability_threshold == 0.7

    def test_judge_config_missing_model_raises(self):
        """Test that missing model raises ValueError."""
        with pytest.raises(ValueError, match="must have a 'model'"):
            ParallelRulesJudgeConfig.from_dict({})

    def test_judge_config_invalid_threshold_raises(self):
        """Test that invalid probability threshold raises ValueError."""
        with pytest.raises(ValueError, match="probability_threshold must be between"):
            ParallelRulesJudgeConfig(model="test", probability_threshold=1.5)


class TestParallelRulesUtils:
    """Test utility functions."""

    def test_build_rule_prompt(self):
        """Test build_rule_prompt creates correct messages."""
        rule = RuleConfig(
            name="test",
            ruletext="Is this harmful?",
            response_types=frozenset([ResponseType.TEXT]),
        )
        prompt = build_rule_prompt(rule, "Hello world")

        assert len(prompt) == 1
        assert prompt[0]["role"] == "user"
        assert "Is this harmful?" in prompt[0]["content"]
        assert "Hello world" in prompt[0]["content"]

    def test_build_rule_prompt_custom_template(self):
        """Test build_rule_prompt with custom template."""
        rule = RuleConfig(
            name="test",
            ruletext="Check profanity",
            response_types=frozenset([ResponseType.TEXT]),
            judge_prompt_template="RULE: {ruletext}\nCONTENT: {content}",
        )
        prompt = build_rule_prompt(rule, "Test content")

        assert "RULE: Check profanity" in prompt[0]["content"]
        assert "CONTENT: Test content" in prompt[0]["content"]

    def test_parse_rule_response_valid_json(self):
        """Test parse_rule_response with valid JSON."""
        result = parse_rule_response('{"probability": 0.8, "explanation": "Contains profanity"}')
        assert result["probability"] == 0.8
        assert result["explanation"] == "Contains profanity"

    def test_parse_rule_response_fenced_json(self):
        """Test parse_rule_response strips code fences."""
        result = parse_rule_response('```json\n{"probability": 0.5, "explanation": "test"}\n```')
        assert result["probability"] == 0.5

    def test_parse_rule_response_invalid_json_raises(self):
        """Test parse_rule_response raises on invalid JSON."""
        with pytest.raises(ValueError, match="JSON parsing failed"):
            parse_rule_response("not valid json")

    def test_format_violation_message_single_violation(self):
        """Test format_violation_message with single violation."""
        rule = RuleConfig(
            name="no_profanity",
            ruletext="Check profanity",
            response_types=frozenset([ResponseType.TEXT]),
            violation_response=ViolationResponseConfig(
                static_message="Profanity detected!",
                include_llm_explanation=True,
            ),
        )
        result = RuleResult(
            probability=0.9,
            explanation="Contains bad words",
            prompt=[],
            response_text="",
        )
        violation = RuleViolation(rule=rule, result=result)

        message = format_violation_message([violation], "original content")

        assert "Profanity detected!" in message
        assert "no_profanity" in message
        assert "Contains bad words" in message

    def test_format_violation_message_multiple_violations(self):
        """Test format_violation_message with multiple violations."""
        rule1 = RuleConfig(
            name="rule1",
            ruletext="Rule 1",
            response_types=frozenset([ResponseType.TEXT]),
            violation_response=ViolationResponseConfig(static_message="Violation 1"),
        )
        rule2 = RuleConfig(
            name="rule2",
            ruletext="Rule 2",
            response_types=frozenset([ResponseType.TEXT]),
            violation_response=ViolationResponseConfig(static_message="Violation 2"),
        )
        result = RuleResult(probability=0.9, explanation="test", prompt=[], response_text="")

        violations = [
            RuleViolation(rule=rule1, result=result),
            RuleViolation(rule=rule2, result=result),
        ]

        message = format_violation_message(violations, "original")

        assert "Violation 1" in message
        assert "Violation 2" in message

    def test_format_violation_message_include_original(self):
        """Test format_violation_message includes original content when configured."""
        rule = RuleConfig(
            name="test",
            ruletext="test",
            response_types=frozenset([ResponseType.TEXT]),
            violation_response=ViolationResponseConfig(
                include_original=True,
                static_message="Blocked",
            ),
        )
        result = RuleResult(probability=0.9, explanation="test", prompt=[], response_text="")
        violation = RuleViolation(rule=rule, result=result)

        message = format_violation_message([violation], "original content here")

        assert "[Original response]" in message
        assert "original content here" in message
        assert "[Policy violations]" in message

    def test_format_violation_message_error_violation(self):
        """Test format_violation_message handles error violations."""
        rule = RuleConfig(
            name="test",
            ruletext="test",
            response_types=frozenset([ResponseType.TEXT]),
            violation_response=ViolationResponseConfig(include_llm_explanation=True),
        )
        violation = RuleViolation(
            rule=rule,
            result=None,
            error=Exception("Judge failed"),
        )

        message = format_violation_message([violation], "original")

        assert "evaluation failed" in message
        assert "fail-secure" in message


class TestParallelRulesPolicyInit:
    """Test policy initialization and configuration."""

    def test_init_requires_judge(self):
        """Test that init raises without judge config."""
        with pytest.raises(ValueError, match="requires 'judge'"):
            ParallelRulesPolicy(rules=[{"name": "test", "ruletext": "test"}])

    def test_init_requires_rules(self):
        """Test that init raises without rules."""
        with pytest.raises(ValueError, match="requires at least one rule"):
            ParallelRulesPolicy(judge={"model": "test"})

    def test_init_requires_nonempty_rules(self):
        """Test that init raises with empty rules list."""
        with pytest.raises(ValueError, match="requires at least one rule"):
            ParallelRulesPolicy(judge={"model": "test"}, rules=[])

    def test_init_parses_configuration(self):
        """Test that init correctly parses configuration."""
        policy = ParallelRulesPolicy(
            judge={
                "model": "claude-3-haiku",
                "probability_threshold": 0.7,
            },
            rules=[
                {
                    "name": "rule1",
                    "ruletext": "Check for profanity",
                    "response_types": ["text"],
                },
                {
                    "name": "rule2",
                    "ruletext": "Check for PII",
                    "response_types": ["text", "tool_call"],
                },
            ],
        )

        assert policy._judge_config.model == "claude-3-haiku"
        assert policy._judge_config.probability_threshold == 0.7
        assert len(policy._rules) == 2
        assert policy._rules[0].name == "rule1"
        assert policy._rules[1].name == "rule2"

    def test_short_policy_name(self):
        """Test short_policy_name property."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test"}],
        )
        assert policy.short_policy_name == "ParallelRules"


class TestParallelRulesPolicyStreaming:
    """Test streaming behavior."""

    @pytest.mark.asyncio
    async def test_on_chunk_received_does_not_forward(self):
        """Test that on_chunk_received doesn't push chunks (handlers do it)."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test"}],
        )
        ctx = create_mock_streaming_context()

        await policy.on_chunk_received(ctx)

        ctx.egress_queue.put.assert_not_called()
        ctx.egress_queue.put_nowait.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_content_delta_buffers(self):
        """Test that on_content_delta doesn't forward immediately."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test"}],
        )
        content_chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[StreamingChoices(index=0, delta=Delta(content="hello"), finish_reason=None)],
        )
        ctx = create_mock_streaming_context(raw_chunks=[content_chunk])

        await policy.on_content_delta(ctx)

        # Should not forward - waiting for completion
        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_content_complete_no_violations_forwards(self):
        """Test that content is forwarded when no rules are violated."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test", "response_types": ["text"]}],
        )

        content_block = ContentStreamBlock(id="content")
        content_block.content = "Hello world"
        content_block.is_complete = True

        ctx = create_mock_streaming_context(
            just_completed=content_block,
            finish_reason="stop",
        )

        async def mock_call_judge(rule, content, config):
            return RuleResult(
                probability=0.1,  # Low probability = no violation
                explanation="Content is fine",
                prompt=[],
                response_text="",
            )

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_content_complete(ctx)

        # Should forward the content
        assert ctx.egress_queue.put.call_count == 1
        sent_chunk = ctx.egress_queue.put.call_args[0][0]
        assert sent_chunk.choices[0].delta.content == "Hello world"

    @pytest.mark.asyncio
    async def test_on_content_complete_with_violation_blocks(self):
        """Test that content is blocked when rules are violated."""
        policy = ParallelRulesPolicy(
            judge={"model": "test", "probability_threshold": 0.5},
            rules=[
                {
                    "name": "test_rule",
                    "ruletext": "test",
                    "response_types": ["text"],
                    "violation_response": {"static_message": "BLOCKED"},
                }
            ],
        )

        content_block = ContentStreamBlock(id="content")
        content_block.content = "Bad content"
        content_block.is_complete = True

        ctx = create_mock_streaming_context(just_completed=content_block)

        async def mock_call_judge(rule, content, config):
            return RuleResult(
                probability=0.9,  # High probability = violation
                explanation="Violation detected",
                prompt=[],
                response_text="",
            )

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_content_complete(ctx)

        # Should send violation response (2 chunks: content + finish)
        assert ctx.egress_queue.put.call_count == 2
        first_chunk = ctx.egress_queue.put.call_args_list[0][0][0]
        assert "BLOCKED" in first_chunk.choices[0].delta.content

    @pytest.mark.asyncio
    async def test_on_tool_call_delta_buffers(self):
        """Test that tool call deltas are buffered in scratchpad."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test"}],
        )

        tc = ChatCompletionDeltaToolCall(
            id="call-123",
            type="function",
            index=0,
            function=Function(name="test_tool", arguments='{"arg":'),
        )
        chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[StreamingChoices(index=0, delta=Delta(tool_calls=[tc]), finish_reason=None)],
        )

        ctx = create_mock_streaming_context(transaction_id="test-call", raw_chunks=[chunk])

        await policy.on_tool_call_delta(ctx)

        # Verify buffered in scratchpad (keyed by index, not tuple)
        buffered_tool_calls = ctx.policy_ctx.scratchpad.get("parallel_rules_buffered_tool_calls", {})
        assert 0 in buffered_tool_calls
        buffered = buffered_tool_calls[0]
        assert buffered["id"] == "call-123"
        assert buffered["name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_no_violations_forwards(self):
        """Test that tool calls are forwarded when no rules are violated."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test", "response_types": ["tool_call"]}],
        )

        block = ToolCallStreamBlock(
            id="call-123",
            index=0,
            name="safe_tool",
            arguments='{"safe": true}',
        )
        block.is_complete = True

        ctx = create_mock_streaming_context(
            transaction_id="test-call",
            just_completed=block,
        )

        # Buffer the tool call in the scratchpad (keyed by index)
        ctx.policy_ctx.scratchpad["parallel_rules_buffered_tool_calls"] = {
            0: {
                "id": "call-123",
                "type": "function",
                "name": "safe_tool",
                "arguments": '{"safe": true}',
            }
        }

        async def mock_call_judge(rule, content, config):
            return RuleResult(probability=0.1, explanation="Safe", prompt=[], response_text="")

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_tool_call_complete(ctx)

        # Should forward tool call
        assert ctx.egress_queue.put.call_count == 1


class TestParallelRulesPolicyNonStreaming:
    """Test non-streaming response handling."""

    @pytest.mark.asyncio
    async def test_on_response_no_violations_passthrough(self):
        """Test that responses pass through when no violations."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test", "response_types": ["text"]}],
        )

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
        )

        async def mock_call_judge(rule, content, config):
            return RuleResult(probability=0.1, explanation="OK", prompt=[], response_text="")

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_with_violation_blocks(self):
        """Test that responses are blocked when rules violated."""
        policy = ParallelRulesPolicy(
            judge={"model": "test", "probability_threshold": 0.5},
            rules=[
                {
                    "name": "test",
                    "ruletext": "test",
                    "response_types": ["text"],
                    "violation_response": {"static_message": "BLOCKED"},
                }
            ],
        )

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "Bad content"}, "finish_reason": "stop"}],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
        )

        async def mock_call_judge(rule, content, config):
            return RuleResult(probability=0.9, explanation="Violation", prompt=[], response_text="")

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        assert result is not response
        assert "BLOCKED" in result.choices[0].message.content


class TestParallelRulesPolicyParallelExecution:
    """Test parallel rule evaluation."""

    @pytest.mark.asyncio
    async def test_multiple_rules_evaluated_in_parallel(self):
        """Test that multiple rules are evaluated in parallel."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[
                {"name": "rule1", "ruletext": "Rule 1", "response_types": ["text"]},
                {"name": "rule2", "ruletext": "Rule 2", "response_types": ["text"]},
                {"name": "rule3", "ruletext": "Rule 3", "response_types": ["text"]},
            ],
        )

        call_count = 0

        async def mock_call_judge(rule, content, config):
            nonlocal call_count
            call_count += 1
            return RuleResult(probability=0.1, explanation="OK", prompt=[], response_text="")

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "Test"}, "finish_reason": "stop"}],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
        )

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_response(response, ctx)

        # All 3 rules should be called
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_rules_filtered_by_response_type(self):
        """Test that only applicable rules are evaluated."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[
                {"name": "text_rule", "ruletext": "Text", "response_types": ["text"]},
                {"name": "tool_rule", "ruletext": "Tool", "response_types": ["tool_call"]},
            ],
        )

        evaluated_rules: list[str] = []

        async def mock_call_judge(rule, content, config):
            evaluated_rules.append(rule.name)
            return RuleResult(probability=0.1, explanation="OK", prompt=[], response_text="")

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "Test"}, "finish_reason": "stop"}],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
        )

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_response(response, ctx)

        # Only text_rule should be evaluated for text content
        assert evaluated_rules == ["text_rule"]

    @pytest.mark.asyncio
    async def test_aggregates_multiple_violations(self):
        """Test that multiple violations are aggregated."""
        policy = ParallelRulesPolicy(
            judge={"model": "test", "probability_threshold": 0.5},
            rules=[
                {
                    "name": "rule1",
                    "ruletext": "Rule 1",
                    "response_types": ["text"],
                    "violation_response": {"static_message": "Violation1"},
                },
                {
                    "name": "rule2",
                    "ruletext": "Rule 2",
                    "response_types": ["text"],
                    "violation_response": {"static_message": "Violation2"},
                },
            ],
        )

        async def mock_call_judge(rule, content, config):
            return RuleResult(probability=0.9, explanation=f"{rule.name} violated", prompt=[], response_text="")

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "Bad"}, "finish_reason": "stop"}],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
        )

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        # Both violations should be in the message
        assert "Violation1" in result.choices[0].message.content
        assert "Violation2" in result.choices[0].message.content


class TestParallelRulesPolicyErrorHandling:
    """Test error handling and fail-secure behavior."""

    @pytest.mark.asyncio
    async def test_judge_error_treated_as_violation(self):
        """Test that judge errors result in violations (fail-secure)."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[
                {
                    "name": "test",
                    "ruletext": "test",
                    "response_types": ["text"],
                    "violation_response": {"include_llm_explanation": True},
                }
            ],
        )

        async def mock_call_judge(rule, content, config):
            raise Exception("Judge service unavailable")

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "Test"}, "finish_reason": "stop"}],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
        )

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        # Should block (fail-secure)
        assert result is not response
        assert "fail-secure" in result.choices[0].message.content.lower()


class TestParallelRulesPolicyCleanup:
    """Test cleanup and state isolation behavior."""

    @pytest.mark.asyncio
    async def test_cleanup_runs_without_error(self):
        """Test that on_streaming_policy_complete runs without error."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test"}],
        )

        ctx = create_mock_streaming_context(transaction_id="test-call")
        # Set up some state in scratchpad
        ctx.policy_ctx.scratchpad["parallel_rules_buffered_tool_calls"] = {0: {"id": "call-1"}}
        ctx.policy_ctx.scratchpad["parallel_rules_blocked"] = True

        # Should run without error (state is in scratchpad, cleaned up per-request)
        await policy.on_streaming_policy_complete(ctx)

    @pytest.mark.asyncio
    async def test_scratchpad_state_isolated_per_request(self):
        """Test that scratchpad state is isolated between different requests."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test"}],
        )

        # Create two separate contexts (simulating concurrent requests)
        ctx1 = create_mock_streaming_context(transaction_id="request-1")
        ctx2 = create_mock_streaming_context(transaction_id="request-2")

        # Use policy's helper methods to set state
        policy._set_blocked(ctx1.policy_ctx)

        # ctx2 should not be affected
        assert not policy._is_blocked(ctx2.policy_ctx)

        # Verify ctx1 is still blocked
        assert policy._is_blocked(ctx1.policy_ctx)


class TestParallelRulesPolicyKeepalive:
    """Test keepalive behavior during evaluation."""

    @pytest.mark.asyncio
    async def test_keepalive_called_during_evaluation(self):
        """Test that keepalive is called during parallel evaluation."""
        policy = ParallelRulesPolicy(
            judge={"model": "test"},
            rules=[{"name": "test", "ruletext": "test", "response_types": ["text"]}],
        )

        content_block = ContentStreamBlock(id="content")
        content_block.content = "Test"
        content_block.is_complete = True

        ctx = create_mock_streaming_context(
            just_completed=content_block,
            finish_reason="stop",
        )

        async def mock_call_judge(rule, content, config):
            return RuleResult(probability=0.1, explanation="OK", prompt=[], response_text="")

        with patch(
            "luthien_proxy.policies.parallel_rules_policy.call_rule_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_content_complete(ctx)

        # Keepalive should have been called
        assert ctx.keepalive.call_count >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
