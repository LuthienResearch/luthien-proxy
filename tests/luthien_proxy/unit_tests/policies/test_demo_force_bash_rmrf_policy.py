"""Unit tests for DemoForceBashRmRfPolicy.

The policy fabricates a bash `rm -rf` tool_use in both non-streaming and
streaming code paths. These tests pin the emission shape so SDK type drift
or streaming-protocol changes break the test instead of breaking a live demo.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
)
from tests.constants import DEFAULT_TEST_MODEL
from tests.luthien_proxy.fixtures.policy_context import make_policy_context

from luthien_proxy.policies.demo_force_bash_rmrf_policy import DemoForceBashRmRfPolicy
from luthien_proxy.policy_core import AnthropicHookPolicy, BasePolicy


def _upstream_response() -> dict[str, Any]:
    return {
        "id": "msg_upstream_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "ignored upstream text"}],
        "model": DEFAULT_TEST_MODEL,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


class TestProtocolCompliance:
    def test_inherits_from_base_policy(self):
        assert isinstance(DemoForceBashRmRfPolicy(), BasePolicy)

    def test_implements_anthropic_hook_policy(self):
        assert isinstance(DemoForceBashRmRfPolicy(), AnthropicHookPolicy)


class TestConfig:
    def test_default_target_path_expands_home(self):
        policy = DemoForceBashRmRfPolicy()
        assert policy._target_path.startswith("/")
        assert "luthien-demo" in policy._target_path
        assert "~" not in policy._target_path

    def test_target_path_appears_in_command(self):
        policy = DemoForceBashRmRfPolicy(target_path="/tmp/luthien-demo/x")
        assert policy._command == "rm -rf /tmp/luthien-demo/x"

    def test_tool_name_override(self):
        assert DemoForceBashRmRfPolicy(tool_name="Bash")._tool_name == "Bash"

    def test_short_policy_name(self):
        assert DemoForceBashRmRfPolicy().short_policy_name == "DemoForceBashRmRf"


class TestNonStreaming:
    @pytest.mark.asyncio
    async def test_replaces_upstream_with_tool_use(self):
        policy = DemoForceBashRmRfPolicy(target_path="/tmp/luthien-demo/x", tool_name="Bash")
        result = cast(dict[str, Any], await policy.on_anthropic_response(_upstream_response(), make_policy_context()))  # type: ignore[arg-type]

        assert result["stop_reason"] == "tool_use"
        assert result["role"] == "assistant"
        assert result["type"] == "message"
        assert len(result["content"]) == 1
        block = result["content"][0]
        assert block["type"] == "tool_use"
        assert block["name"] == "Bash"
        assert block["input"] == {"command": "rm -rf /tmp/luthien-demo/x"}
        assert block["id"].startswith("toolu_")

    @pytest.mark.parametrize("tool_name", ["Bash", "mcp__workspace__bash"])
    @pytest.mark.asyncio
    async def test_respects_configured_tool_name(self, tool_name: str):
        policy = DemoForceBashRmRfPolicy(tool_name=tool_name)
        result = cast(dict[str, Any], await policy.on_anthropic_response(_upstream_response(), make_policy_context()))  # type: ignore[arg-type]
        assert result["content"][0]["name"] == tool_name


class TestStreaming:
    @pytest.mark.asyncio
    async def test_swallows_upstream_events(self):
        policy = DemoForceBashRmRfPolicy()
        result = await policy.on_anthropic_stream_event(
            cast(Any, RawMessageStopEvent.model_construct(type="message_stop")),
            make_policy_context(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_complete_emits_six_events_in_protocol_order(self):
        policy = DemoForceBashRmRfPolicy(target_path="/tmp/luthien-demo/x", tool_name="Bash")
        events = await policy.on_anthropic_stream_complete(make_policy_context())

        assert [type(ev) for ev in events] == [
            RawMessageStartEvent,
            RawContentBlockStartEvent,
            RawContentBlockDeltaEvent,
            RawContentBlockStopEvent,
            RawMessageDeltaEvent,
            RawMessageStopEvent,
        ]

    @pytest.mark.asyncio
    async def test_complete_tool_use_block_has_configured_name_and_command(self):
        policy = DemoForceBashRmRfPolicy(target_path="/tmp/luthien-demo/x", tool_name="mcp__workspace__bash")
        events = await policy.on_anthropic_stream_complete(make_policy_context())

        # Compare via model_dump to sidestep typed-vs-dict access for fields
        # that the policy constructs with `model_construct` (validation-skipped).
        block_start = cast(Any, events[1]).model_dump()
        assert block_start["content_block"]["type"] == "tool_use"
        assert block_start["content_block"]["name"] == "mcp__workspace__bash"
        assert block_start["content_block"]["id"].startswith("toolu_")

        block_delta = cast(Any, events[2]).model_dump()
        assert block_delta["delta"]["type"] == "input_json_delta"
        assert block_delta["delta"]["partial_json"] == '{"command": "rm -rf /tmp/luthien-demo/x"}'

    @pytest.mark.asyncio
    async def test_complete_message_delta_signals_tool_use_stop(self):
        policy = DemoForceBashRmRfPolicy()
        events = await policy.on_anthropic_stream_complete(make_policy_context())

        message_delta = cast(Any, events[4]).model_dump()
        assert message_delta["delta"]["stop_reason"] == "tool_use"
