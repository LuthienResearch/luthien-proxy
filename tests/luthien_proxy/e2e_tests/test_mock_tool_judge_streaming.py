"""Mock e2e regression: ToolCallJudgePolicy streaming rewrites stop_reason.

When every tool_use is blocked by the judge, the streaming `message_delta`
must report `stop_reason="end_turn"` (not `"tool_use"`). Otherwise downstream
consumers (e.g. Claude Code) read `stop_reason: tool_use`, expect a tool_use
content block to invoke, find only the substituted text, and bail out with
"The model's tool call could not be parsed (retry also failed)".

Trello: https://trello.com/c/zjzq6aP5

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_tool_judge_streaming.py -v
"""

import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator

pytestmark = pytest.mark.mock_e2e

_TOOL_JUDGE_POLICY = "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"

# Unreachable judge URL → ToolCallJudgePolicy is fail-secure: every tool call
# raises in _call_judge, the policy returns probability=1.0, and the tool is
# blocked. Deterministic without paying for real-API CI on every run.
_UNREACHABLE_JUDGE_CONFIG = {
    "model": "claude-haiku-4-5",
    "api_base": "http://127.0.0.1:19999",
    "auth_provider": "user_credentials",
    "probability_threshold": 0.5,
    "blocked_message_template": "⛔ TEST_BLOCK: Tool '{tool_name}' rejected",
}


@pytest.mark.asyncio
async def test_streaming_stop_reason_rewritten_when_tool_blocked(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Blocked tool_use in a streaming response → terminal message_delta.stop_reason == 'end_turn'."""
    mock_anthropic.enqueue(tool_response("Read", {"file_path": "/tmp/x.txt"}))

    async with policy_context(
        _TOOL_JUDGE_POLICY, _UNREACHABLE_JUDGE_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn = await session.send("Read the file")

    # The blocked-message text should appear (tool_use replaced with text block).
    assert "⛔ TEST_BLOCK" in turn.text, f"Expected blocked-message text, got: {turn.text!r}"

    # No tool_use blocks should survive — the bug fix's primary invariant.
    assert not turn.has_tool_calls, f"Expected no tool calls (all blocked), got {turn.tool_calls}"

    # The bug: terminal message_delta carried stop_reason='tool_use' even though
    # no tool_use block was emitted. After the fix, it's rewritten to 'end_turn'.
    assert turn.stop_reason == "end_turn", (
        f"Expected stop_reason='end_turn' after blocking all tool_use, got {turn.stop_reason!r}"
    )

    # Direct SSE assertion: scan raw events for the message_delta and verify.
    msg_deltas = [e for e in turn.raw_events if e.get("type") == "message_delta"]
    assert len(msg_deltas) == 1, f"Expected exactly one message_delta event, got {len(msg_deltas)}"
    assert msg_deltas[0]["delta"]["stop_reason"] == "end_turn"
