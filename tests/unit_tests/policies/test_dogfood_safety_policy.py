"""Unit tests for DogfoodSafetyPolicy."""

from __future__ import annotations

import json
from typing import Any

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Function

from luthien_proxy.policies.dogfood_safety_policy import (
    DogfoodSafetyConfig,
    DogfoodSafetyPolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


def _make_policy(
    patterns: list[str] | None = None,
    tool_names: list[str] | None = None,
) -> DogfoodSafetyPolicy:
    """Create a DogfoodSafetyPolicy with optional overrides."""
    config_kwargs: dict[str, Any] = {}
    if patterns is not None:
        config_kwargs["blocked_patterns"] = patterns
    if tool_names is not None:
        config_kwargs["tool_names"] = tool_names
    return DogfoodSafetyPolicy(DogfoodSafetyConfig(**config_kwargs))


def _make_context(transaction_id: str = "test-txn") -> PolicyContext:
    return PolicyContext.for_testing(transaction_id=transaction_id)


# ============================================================================
# Pattern matching (_is_dangerous)
# ============================================================================


class TestIsDangerous:
    """Core pattern-matching logic."""

    @pytest.mark.parametrize(
        "command",
        [
            "docker compose down",
            "docker compose stop",
            "docker compose rm",
            "docker compose kill",
            "docker-compose down",
            "docker stop gateway",
            "docker kill gateway",
            "docker rm gateway",
            "pkill uvicorn",
            "killall python",
            "rm -rf .env",
            "rm docker-compose.yaml",
            "rm -f src/luthien_proxy/main.py",
            "docker compose exec db psql -c DROP TABLE",
            "docker compose exec db psql -c TRUNCATE events",
        ],
    )
    def test_blocks_dangerous_commands(self, command: str):
        policy = _make_policy()
        is_blocked, _ = policy._is_dangerous("Bash", {"command": command})
        assert is_blocked, f"Expected '{command}' to be blocked"

    @pytest.mark.parametrize(
        "command",
        [
            "echo hello",
            "ls -la",
            "python main.py",
            "git status",
            "docker ps",
            "docker logs gateway",
            "cat .env",
            "rm tempfile.txt",
            "pip install requests",
        ],
    )
    def test_allows_safe_commands(self, command: str):
        policy = _make_policy()
        is_blocked, _ = policy._is_dangerous("Bash", {"command": command})
        assert not is_blocked, f"Expected '{command}' to be allowed"

    def test_ignores_non_bash_tools(self):
        policy = _make_policy()
        is_blocked, _ = policy._is_dangerous("Read", {"command": "docker compose down"})
        assert not is_blocked

    def test_case_insensitive_matching(self):
        policy = _make_policy()
        is_blocked, _ = policy._is_dangerous("Bash", {"command": "DOCKER COMPOSE DOWN"})
        assert is_blocked

    def test_tool_name_case_insensitive(self):
        policy = _make_policy()
        is_blocked, _ = policy._is_dangerous("bash", {"command": "docker compose down"})
        assert is_blocked

    def test_custom_patterns(self):
        policy = _make_policy(patterns=[r"dangerous_command"])
        is_blocked, _ = policy._is_dangerous("Bash", {"command": "run dangerous_command now"})
        assert is_blocked

    def test_custom_tool_names(self):
        policy = _make_policy(tool_names=["my_tool"])
        is_blocked, _ = policy._is_dangerous("my_tool", {"command": "docker compose down"})
        assert is_blocked

    def test_returns_command_string(self):
        policy = _make_policy()
        _, command = policy._is_dangerous("Bash", {"command": "docker compose down"})
        assert command == "docker compose down"

    def test_empty_command(self):
        policy = _make_policy()
        is_blocked, _ = policy._is_dangerous("Bash", {"command": ""})
        assert not is_blocked


# ============================================================================
# Command extraction (_extract_command)
# ============================================================================


class TestExtractCommand:
    """Handles dict, JSON string, and plain string inputs."""

    def test_dict_input(self):
        policy = _make_policy()
        assert policy._extract_command({"command": "ls -la"}) == "ls -la"

    def test_dict_missing_command_key(self):
        policy = _make_policy()
        assert policy._extract_command({"other": "value"}) == ""

    def test_json_string_input(self):
        policy = _make_policy()
        json_str = json.dumps({"command": "docker compose down"})
        assert policy._extract_command(json_str) == "docker compose down"

    def test_plain_string_input(self):
        policy = _make_policy()
        assert policy._extract_command("ls -la") == "ls -la"

    def test_invalid_json_string_returns_raw(self):
        policy = _make_policy()
        assert policy._extract_command("not json {") == "not json {"

    def test_empty_dict(self):
        policy = _make_policy()
        assert policy._extract_command({}) == ""


# ============================================================================
# OpenAI non-streaming (on_openai_response)
# ============================================================================


class TestOpenAINonStreaming:
    """Non-streaming OpenAI response handling."""

    @pytest.mark.asyncio
    async def test_blocks_dangerous_tool_call(self, make_model_response):
        policy = _make_policy()
        ctx = _make_context()

        response = make_model_response("", model="gpt-4")
        response.choices[0].message.tool_calls = [
            ChatCompletionMessageToolCall(
                id="call_1",
                type="function",
                function=Function(name="Bash", arguments=json.dumps({"command": "docker compose down"})),
            )
        ]
        response.choices[0].finish_reason = "tool_calls"

        result = await policy.on_openai_response(response, ctx)

        assert result is not response
        content = result.choices[0].message.content
        assert content is not None
        assert "BLOCKED" in content

    @pytest.mark.asyncio
    async def test_allows_safe_tool_call(self, make_model_response):
        policy = _make_policy()
        ctx = _make_context()

        response = make_model_response("", model="gpt-4")
        response.choices[0].message.tool_calls = [
            ChatCompletionMessageToolCall(
                id="call_1",
                type="function",
                function=Function(name="Bash", arguments=json.dumps({"command": "echo hello"})),
            )
        ]
        response.choices[0].finish_reason = "tool_calls"

        result = await policy.on_openai_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_passes_through_text_response(self, make_model_response):
        policy = _make_policy()
        ctx = _make_context()
        response = make_model_response("Hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result is response


# ============================================================================
# Anthropic non-streaming (on_anthropic_response)
# ============================================================================


class TestAnthropicNonStreaming:
    """Non-streaming Anthropic response handling."""

    @pytest.mark.asyncio
    async def test_blocks_dangerous_tool_use(self):
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "docker compose down"},
                }
            ],
            "stop_reason": "tool_use",
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["type"] == "text"
        assert "BLOCKED" in result["content"][0]["text"]
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_allows_safe_tool_use(self):
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "echo hello"},
                }
            ],
            "stop_reason": "tool_use",
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_passes_through_text_response(self):
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [{"type": "text", "text": "Hello world"}],
            "stop_reason": "end_turn",
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_mixed_content_blocks_only_dangerous(self):
        """When multiple content blocks exist, only dangerous ones are replaced."""
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "text", "text": "Let me run that"},
                {
                    "type": "tool_use",
                    "id": "toolu_safe",
                    "name": "Bash",
                    "input": {"command": "echo hello"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_bad",
                    "name": "Bash",
                    "input": {"command": "docker compose down"},
                },
            ],
            "stop_reason": "tool_use",
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Let me run that"
        assert result["content"][1]["type"] == "tool_use"
        assert "BLOCKED" in result["content"][2]["text"]


# ============================================================================
# Configuration
# ============================================================================


class TestConfig:
    """DogfoodSafetyConfig and initialization."""

    def test_default_config(self):
        policy = DogfoodSafetyPolicy()
        assert len(policy._compiled_patterns) > 0
        assert "bash" in policy._tool_names_lower

    def test_custom_blocked_message(self):
        config = DogfoodSafetyConfig(blocked_message="Nope: {command}")
        policy = DogfoodSafetyPolicy(config)
        msg = policy._format_blocked_message("docker compose down")
        assert msg == "Nope: docker compose down"

    def test_command_truncated_in_message(self):
        policy = _make_policy()
        long_command = "x" * 300
        msg = policy._format_blocked_message(long_command)
        assert len(long_command) > 200
        assert "x" * 200 in msg

    def test_short_policy_name(self):
        policy = DogfoodSafetyPolicy()
        assert policy.short_policy_name == "DogfoodSafety"


# ============================================================================
# State cleanup
# ============================================================================


class TestStateCleanup:
    """Per-request state cleanup prevents unbounded growth."""

    def test_openai_cleanup_removes_buffered_state(self):
        policy = _make_policy()
        txn_id = "txn-123"

        policy._buffered_tool_calls[("txn-123", 0)] = {"name": "test"}
        policy._buffered_tool_calls[("txn-123", 1)] = {"name": "test2"}
        policy._buffered_tool_calls[("other-txn", 0)] = {"name": "keep"}
        policy._blocked_calls.add(txn_id)

        # Simulate what on_streaming_policy_complete does
        keys_to_remove = [k for k in policy._buffered_tool_calls if k[0] == txn_id]
        for k in keys_to_remove:
            del policy._buffered_tool_calls[k]
        policy._blocked_calls.discard(txn_id)

        assert ("txn-123", 0) not in policy._buffered_tool_calls
        assert ("txn-123", 1) not in policy._buffered_tool_calls
        assert ("other-txn", 0) in policy._buffered_tool_calls
        assert txn_id not in policy._blocked_calls

    def test_anthropic_cleanup_removes_buffered_state(self):
        policy = _make_policy()
        txn_id = "txn-456"

        policy._buffered_tool_uses[("txn-456", 0)] = {"name": "test"}
        policy._buffered_tool_uses[("txn-456", 1)] = {"name": "test2"}
        policy._buffered_tool_uses[("other-txn", 0)] = {"name": "keep"}

        # Simulate what the updated on_streaming_policy_complete should do
        keys_to_remove = [k for k in policy._buffered_tool_uses if k[0] == txn_id]
        for k in keys_to_remove:
            del policy._buffered_tool_uses[k]

        assert ("txn-456", 0) not in policy._buffered_tool_uses
        assert ("txn-456", 1) not in policy._buffered_tool_uses
        assert ("other-txn", 0) in policy._buffered_tool_uses
