"""Tests for DogfoodSafetyPolicy — pattern-matching safety for dogfooding."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    ToolUseBlock,
)

from luthien_proxy.policies.dogfood_safety_policy import (
    DogfoodSafetyConfig,
    DogfoodSafetyPolicy,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def policy() -> DogfoodSafetyPolicy:
    return DogfoodSafetyPolicy()


@pytest.fixture
def mock_context() -> MagicMock:
    ctx = MagicMock()
    ctx.record_event = MagicMock()
    ctx.transaction_id = "test-txn-123"
    return ctx


# ============================================================================
# Pattern matching — core logic
# ============================================================================


class TestPatternMatching:
    """Test the _is_dangerous method with various commands."""

    def test_docker_compose_down_blocked(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, cmd = policy._is_dangerous("Bash", {"command": "docker compose down"})
        assert blocked
        assert "docker compose down" in cmd

    def test_docker_compose_stop_blocked(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose stop"})
        assert blocked

    def test_docker_compose_down_with_flags(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose down -v --remove-orphans"})
        assert blocked

    def test_docker_stop_container(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker stop luthien-gateway-1"})
        assert blocked

    def test_docker_kill_container(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker kill luthien-gateway-1"})
        assert blocked

    def test_docker_compose_kill(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose kill gateway"})
        assert blocked

    def test_docker_compose_legacy_down(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker-compose down"})
        assert blocked

    def test_pkill_uvicorn(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "pkill -f uvicorn"})
        assert blocked

    def test_killall_python(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "killall python"})
        assert blocked

    def test_rm_env_file(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "rm .env"})
        assert blocked

    def test_rm_rf_src(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "rm -rf src/luthien"})
        assert blocked

    def test_docker_exec_drop_table(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose exec db psql -c 'DROP TABLE events'"})
        assert blocked

    # --- Safe commands that should pass ---

    def test_docker_compose_logs_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose logs -f gateway"})
        assert not blocked

    def test_docker_compose_ps_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose ps"})
        assert not blocked

    def test_docker_compose_up_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose up -d gateway"})
        assert not blocked

    def test_docker_compose_restart_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        """docker compose restart is allowed — it doesn't kill, just restarts."""
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose restart gateway"})
        assert not blocked

    def test_git_status_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "git status"})
        assert not blocked

    def test_ls_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "ls -la"})
        assert not blocked

    def test_rm_tmp_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "rm -rf /tmp/test"})
        assert not blocked

    def test_non_bash_tool_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        """Non-bash tools should always pass through."""
        blocked, _ = policy._is_dangerous("Read", {"file_path": "/etc/passwd"})
        assert not blocked

    def test_edit_tool_allowed(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Edit", {"command": "docker compose down"})
        assert not blocked

    # --- Input format handling ---

    def test_json_string_input(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", json.dumps({"command": "docker compose down"}))
        assert blocked

    def test_empty_command(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": ""})
        assert not blocked

    def test_missing_command_key(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"other_field": "docker compose down"})
        assert not blocked

    def test_case_insensitive_matching(self, policy: DogfoodSafetyPolicy) -> None:
        blocked, _ = policy._is_dangerous("Bash", {"command": "Docker Compose Down"})
        assert blocked


class TestCustomConfig:
    """Test custom configuration."""

    def test_custom_patterns(self) -> None:
        config = DogfoodSafetyConfig(blocked_patterns=[r"my_dangerous_cmd"])
        policy = DogfoodSafetyPolicy(config=config)
        blocked, _ = policy._is_dangerous("Bash", {"command": "my_dangerous_cmd --force"})
        assert blocked
        # Default patterns should not match
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose down"})
        assert not blocked

    def test_custom_tool_names(self) -> None:
        config = DogfoodSafetyConfig(tool_names=["run_command"])
        policy = DogfoodSafetyPolicy(config=config)
        blocked, _ = policy._is_dangerous("run_command", {"command": "docker compose down"})
        assert blocked
        # Default tool name "Bash" should not match
        blocked, _ = policy._is_dangerous("Bash", {"command": "docker compose down"})
        assert not blocked


# ============================================================================
# Anthropic non-streaming
# ============================================================================


class TestAnthropicNonStreaming:
    @pytest.mark.asyncio
    async def test_blocks_dangerous_tool_use(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "Bash",
                    "input": {"command": "docker compose down"},
                }
            ],
            "stop_reason": "tool_use",
        }
        result = await policy.on_anthropic_response(response, mock_context)  # type: ignore[arg-type]
        # Tool_use should be replaced with text
        assert result["content"][0]["type"] == "text"
        assert "BLOCKED" in result["content"][0]["text"]
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_allows_safe_tool_use(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "Bash",
                    "input": {"command": "git status"},
                }
            ],
            "stop_reason": "tool_use",
        }
        result = await policy.on_anthropic_response(response, mock_context)  # type: ignore[arg-type]
        assert result["content"][0]["type"] == "tool_use"
        assert result["stop_reason"] == "tool_use"

    @pytest.mark.asyncio
    async def test_passes_text_blocks_unchanged(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        response: dict[str, Any] = {
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
        }
        result = await policy.on_anthropic_response(response, mock_context)  # type: ignore[arg-type]
        assert result is response


# ============================================================================
# Anthropic streaming
# ============================================================================


class TestAnthropicStreaming:
    @pytest.mark.asyncio
    async def test_buffers_tool_use_start(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        tool_block = ToolUseBlock(type="tool_use", id="tool_1", name="Bash", input={})
        event = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=tool_block)
        result = await policy.on_anthropic_stream_event(event, mock_context)
        assert result == []
        assert (mock_context.transaction_id, 0) in policy._buffered_tool_uses

    @pytest.mark.asyncio
    async def test_buffers_input_json_delta(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        # Set up buffer first (keyed by transaction_id + index)
        key = (mock_context.transaction_id, 0)
        policy._buffered_tool_uses[key] = {"id": "tool_1", "name": "Bash", "input_json": ""}
        delta = InputJSONDelta(type="input_json_delta", partial_json='{"command": "docker compose down"}')
        event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta)
        result = await policy.on_anthropic_stream_event(event, mock_context)
        assert result == []
        assert "docker compose down" in policy._buffered_tool_uses[key]["input_json"]

    @pytest.mark.asyncio
    async def test_blocks_dangerous_on_stop(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        key = (mock_context.transaction_id, 0)
        policy._buffered_tool_uses[key] = {
            "id": "tool_1",
            "name": "Bash",
            "input_json": '{"command": "docker compose down"}',
        }
        event = RawContentBlockStopEvent(type="content_block_stop", index=0)
        result = await policy.on_anthropic_stream_event(event, mock_context)

        # Should get text replacement events
        assert len(result) == 3
        assert isinstance(result[0], RawContentBlockStartEvent)
        assert isinstance(result[0].content_block, TextBlock)
        assert isinstance(result[1], RawContentBlockDeltaEvent)
        assert "BLOCKED" in result[1].delta.text  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_allows_safe_on_stop(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        key = (mock_context.transaction_id, 0)
        policy._buffered_tool_uses[key] = {
            "id": "tool_1",
            "name": "Bash",
            "input_json": '{"command": "git status"}',
        }
        event = RawContentBlockStopEvent(type="content_block_stop", index=0)
        result = await policy.on_anthropic_stream_event(event, mock_context)

        # Should get reconstructed tool_use events
        assert len(result) == 3
        assert isinstance(result[0], RawContentBlockStartEvent)
        assert isinstance(result[0].content_block, ToolUseBlock)

    @pytest.mark.asyncio
    async def test_text_events_pass_through(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        text_block = TextBlock(type="text", text="")
        event = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=text_block)
        result = await policy.on_anthropic_stream_event(event, mock_context)
        assert result == [event]

    @pytest.mark.asyncio
    async def test_concurrent_requests_isolated(self, policy: DogfoodSafetyPolicy) -> None:
        """Two concurrent requests at the same block index must not corrupt each other."""
        ctx_a = MagicMock()
        ctx_a.transaction_id = "txn-a"
        ctx_a.record_event = MagicMock()
        ctx_b = MagicMock()
        ctx_b.transaction_id = "txn-b"
        ctx_b.record_event = MagicMock()

        tool_block = ToolUseBlock(type="tool_use", id="tool_1", name="Bash", input={})

        # Both requests start a tool_use at index 0
        start_event = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=tool_block)
        await policy.on_anthropic_stream_event(start_event, ctx_a)
        await policy.on_anthropic_stream_event(start_event, ctx_b)

        # Request A gets a dangerous command, request B gets a safe one
        dangerous_delta = InputJSONDelta(type="input_json_delta", partial_json='{"command": "docker compose down"}')
        safe_delta = InputJSONDelta(type="input_json_delta", partial_json='{"command": "git status"}')
        delta_a = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=dangerous_delta)
        delta_b = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=safe_delta)
        await policy.on_anthropic_stream_event(delta_a, ctx_a)
        await policy.on_anthropic_stream_event(delta_b, ctx_b)

        # Stop events
        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        result_a = await policy.on_anthropic_stream_event(stop, ctx_a)
        result_b = await policy.on_anthropic_stream_event(stop, ctx_b)

        # A should be blocked, B should be allowed
        assert isinstance(result_a[0], RawContentBlockStartEvent)
        assert isinstance(result_a[0].content_block, TextBlock)  # blocked → text replacement
        assert isinstance(result_b[0], RawContentBlockStartEvent)
        assert isinstance(result_b[0].content_block, ToolUseBlock)  # allowed → tool_use passthrough


# ============================================================================
# OpenAI non-streaming
# ============================================================================


class TestOpenAINonStreaming:
    @pytest.mark.asyncio
    async def test_blocks_dangerous_tool_call(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        from litellm.types.utils import ChatCompletionMessageToolCall, Choices, Function, Message, ModelResponse

        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(
                        role="assistant",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                function=Function(
                                    name="Bash",
                                    arguments=json.dumps({"command": "docker compose down"}),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
        )
        result = await policy.on_openai_response(response, mock_context)
        # Should be replaced with text response
        assert result.choices[0].message.content is not None  # type: ignore[union-attr]
        assert "BLOCKED" in str(result.choices[0].message.content)  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_allows_safe_tool_call(self, policy: DogfoodSafetyPolicy, mock_context: MagicMock) -> None:
        from litellm.types.utils import ChatCompletionMessageToolCall, Choices, Function, Message, ModelResponse

        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(
                        role="assistant",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                function=Function(
                                    name="Bash",
                                    arguments=json.dumps({"command": "git status"}),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
        )
        result = await policy.on_openai_response(response, mock_context)
        assert result is response
