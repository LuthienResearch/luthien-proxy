"""E2E test: ToolCallJudgePolicy must correct stop_reason when all tools are blocked.

H5 regression: when ToolCallJudgePolicy blocks all tools in a streaming response,
the message_delta stop_reason must be corrected from 'tool_use' to 'end_turn'.

Without the fix, the stream emits stop_reason='tool_use' with zero tool_use blocks,
which causes the Anthropic API to reject the next turn with 400.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_tool_call_judge_blocked.py -v
"""

import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response, tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator

pytestmark = pytest.mark.mock_e2e

_TOOL_JUDGE_POLICY = "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"

_BLOCKING_JUDGE_CONFIG = {
    "auth_provider": "user_credentials",
    "api_base": "http://127.0.0.1:19999",
    "probability_threshold": 0.5,
}


@pytest.mark.asyncio
async def test_tool_call_judge_blocks_single_tool_session_continues(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """H5: ToolCallJudgePolicy blocks all tools → stop_reason corrected to 'end_turn'.

    The judge is unreachable (probability=1.0 fail-secure) so all tools are blocked.
    Turn 1 stop_reason must be 'end_turn', not 'tool_use'.
    Turn 2 (follow-up user message) must succeed.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "ls"}))
    mock_anthropic.enqueue(text_response("I blocked that tool call."))

    async with policy_context(
        _TOOL_JUDGE_POLICY, _BLOCKING_JUDGE_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn1 = await session.send("Run ls")

        assert len(turn1.tool_calls) == 0, f"Blocked tool must not appear as tool_call, got {turn1.tool_calls}"
        assert turn1.stop_reason == "end_turn", (
            f"Expected 'end_turn' after all tools blocked, got {turn1.stop_reason!r}; "
            "this is the H5 bug: stop_reason='tool_use' with no tool_use blocks"
        )

        turn2 = await session.send("Never mind, just tell me what happened")

        assert turn2.text, f"Turn 2 must return a response, got: {turn2.text!r}"
