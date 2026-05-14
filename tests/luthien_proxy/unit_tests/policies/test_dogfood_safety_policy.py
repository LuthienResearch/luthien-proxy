"""Unit tests for DogfoodSafetyPolicy."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    ToolUseBlock,
)
from tests.luthien_proxy.unit_tests.policies.anthropic_event_builders import message_delta

from luthien_proxy.policies.dogfood_safety_policy import (
    DogfoodSafetyConfig,
    DogfoodSafetyPolicy,
)
from luthien_proxy.policy_core.anthropic_message_builder import AnthropicMessageBuilder
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

        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "tool_use"
        assert result["content"][0]["input"] == {"command": "echo hello"}
        assert result["stop_reason"] == "tool_use"

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
        """When multiple content blocks exist, only dangerous ones are replaced.

        Wire shape: tool_use must trail (#708 invariant). Blocked-text
        replacements land in the pre-tool slot; the surviving safe
        tool_use is the trailing block.
        """
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

        types = [b["type"] for b in result["content"]]
        # All non-tool content (original text + blocked replacement) precedes the surviving tool.
        assert types == ["text", "text", "tool_use"], f"Got: {types}"
        assert result["content"][0]["text"] == "Let me run that"
        assert "BLOCKED" in result["content"][1]["text"]
        assert result["content"][2]["name"] == "Bash"
        assert result["content"][2]["input"] == {"command": "echo hello"}
        assert result["stop_reason"] == "tool_use"


# ============================================================================
# Streaming stop_reason correction
# ============================================================================


def _tool_start(index: int, tool_id: str = "toolu_1", name: str = "Bash") -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=tool_id, name=name, input={}),
    )


def _tool_delta(index: int, partial_json: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=partial_json),
    )


def _block_stop(index: int) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


class TestStreamingStopReasonCorrection:
    """Streaming path must rewrite stop_reason='tool_use' → 'end_turn' when all tool_use blocked.

    Mirrors the non-streaming path's existing behavior (line 215-217) and the
    same fix applied to ToolCallJudgePolicy in this PR.
    """

    @pytest.mark.asyncio
    async def test_stop_reason_corrected_after_dangerous_tool_blocked(self):
        """Single dangerous tool_use blocked → message_delta('tool_use') rewritten to 'end_turn'."""
        policy = _make_policy()
        ctx = _make_context()

        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
        await policy.on_anthropic_stream_event(
            cast(MessageStreamEvent, _tool_delta(0, '{"command":"docker compose down"}')), ctx
        )
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)

        msg_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stop_reason_kept_when_tool_safe(self):
        """Safe tool_use passed through → stop_reason stays 'tool_use'."""
        policy = _make_policy()
        ctx = _make_context()

        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
        await policy.on_anthropic_stream_event(
            cast(MessageStreamEvent, _tool_delta(0, '{"command":"echo hello"}')), ctx
        )
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)

        msg_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_stop_reason_kept_with_mixed_tools(self):
        """Mixed safe + dangerous → stop_reason stays 'tool_use' (safe tool survives)."""
        policy = _make_policy()
        ctx = _make_context()

        # Index 0: safe
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0, tool_id="toolu_safe")), ctx)
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_delta(0, '{"command":"echo hi"}')), ctx)
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)

        # Index 1: dangerous
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(1, tool_id="toolu_bad")), ctx)
        await policy.on_anthropic_stream_event(
            cast(MessageStreamEvent, _tool_delta(1, '{"command":"docker compose down"}')), ctx
        )
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(1)), ctx)

        msg_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_stop_reason_rewritten_when_no_blocks_observed(self):
        """No tool_use observed → stop_reason rewritten to end_turn (#708 invariant)."""
        policy = _make_policy()
        ctx = _make_context()

        msg_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "end_turn"


# ============================================================================
# Fail-secure on malformed input
# ============================================================================


class TestFailSecureOnMalformedInput:
    """Streaming tool_use with malformed input_json must still match dangerous patterns.

    Regression: when `BufferedTool.input_json` fails to parse as JSON, the
    parsed-dict representation is `{"_raw": <string>}`. A safety policy that
    extracted `tool_input["command"]` would see an empty string and fail open.
    The transform falls back to the raw text in that case.
    """

    @pytest.mark.asyncio
    async def test_dangerous_command_in_malformed_json_still_blocked(self):
        policy = _make_policy()
        ctx = _make_context()

        # Truncated JSON: valid-up-to-here but no closing brace. json.loads will fail;
        # the regex must still match `docker compose down` in the raw text.
        all_events: list[MessageStreamEvent] = []
        all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
        all_events += await policy.on_anthropic_stream_event(
            cast(MessageStreamEvent, _tool_delta(0, '{"command":"docker compose down"')), ctx
        )
        all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
        all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        # First emitted block must be the blocked-text replacement, not a tool_use.
        start_event = all_events[0]
        assert isinstance(start_event, RawContentBlockStartEvent)
        assert start_event.content_block.type == "text"


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

    @pytest.mark.asyncio
    async def test_anthropic_cleanup_removes_buffered_state(self):
        policy = _make_policy()
        policy_ctx = _make_context("txn-456")

        # Drive a tool_use through the stream to populate request state.
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), policy_ctx)
        await policy.on_anthropic_stream_event(
            cast(MessageStreamEvent, _tool_delta(0, '{"command":"docker compose down"}')), policy_ctx
        )
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), policy_ctx)

        # Cleanup must remove a populated buffer (not just no-op on empty state).
        await policy.on_anthropic_streaming_policy_complete(policy_ctx)
        assert policy_ctx.pop_request_state(policy, AnthropicMessageBuilder) is None
