"""E2E test: parallel tool_use with judge failure must not brick the session.

Reproduces the bug from the Trello ticket "Safety judge returns 400 on
parallel MCP tool use — blocks session recovery". The scenario:

  1. Backend responds with 2 parallel tool_use blocks
  2. Judge fails (unreachable) → warning injected, tools pass through
  3. Client sends tool_result for both tools
  4. Backend responds with text
  5. Session must remain functional (no 400)

Before the fix, the warning was injected after message_delta in the stream,
violating Anthropic's event ordering. This corrupted the client's
conversation history, causing all subsequent requests to fail with 400.

Run:
    E2E_GATEWAY_URL=http://localhost:8001 \
    E2E_API_KEY=sk-luthien-dev-key \
    E2E_ADMIN_API_KEY=admin-dev-key \
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_simple_llm_parallel_tools.py -v
"""

import pytest
from tests.luthien_proxy.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import parallel_tool_response, text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator
from tests.luthien_proxy.fixtures.anthropic_stream_validator import validate_anthropic_event_ordering

pytestmark = pytest.mark.mock_e2e

_SIMPLE_LLM_POLICY = "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"

# Judge pointed at an unreachable URL to force failure
_UNREACHABLE_JUDGE = {
    "instructions": "Block all content",
    "model": "claude-haiku-4-5",
    "base_url": "http://127.0.0.1:19999",
    "api_key": "fake-key",
}


@pytest.mark.asyncio
async def test_parallel_tool_use_with_judge_failure_session_survives(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """When the judge fails during a response with parallel tool_use blocks,
    the session must remain functional for subsequent turns.

    This is the core regression test for the parallel tool_use 400 bug.
    """
    # Turn 1: backend responds with 2 parallel tool_use blocks
    mock_anthropic.enqueue(
        parallel_tool_response(
            [
                ("Bash", {"command": "ls"}),
                ("Read", {"file_path": "/tmp/test.txt"}),
            ]
        )
    )
    # Turn 2: after tool results, backend responds with text
    mock_anthropic.enqueue(text_response("Here are the results from both tools."))

    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config):
        session = ClaudeCodeSimulator(GATEWAY_URL, API_KEY)

        # Turn 1: send message, get parallel tool_use response
        turn1 = await session.send("List files and read test.txt")

        # Both tool calls should pass through despite judge failure
        assert len(turn1.tool_calls) == 2, (
            f"Expected 2 tool calls to pass through, got {len(turn1.tool_calls)}: {turn1.tool_calls}"
        )
        assert turn1.stop_reason == "tool_use"

        # Warning should be present
        assert "Safety judge unavailable" in turn1.text, f"Expected judge unavailable warning, got: {turn1.text!r}"

        # Verify streaming protocol compliance
        validate_anthropic_event_ordering(turn1.raw_events).assert_valid()

        # Turn 2: send tool results — this is where the session would brick
        # before the fix (400 from malformed conversation history)
        turn2 = await session.continue_with_tool_results(
            [
                (turn1.tool_calls[0].id, "file1.txt\nfile2.txt"),
                (turn1.tool_calls[1].id, "contents of test.txt"),
            ]
        )

        # Session should still be functional
        assert "results from both tools" in turn2.text, f"Expected text response in turn 2, got: {turn2.text!r}"


@pytest.mark.asyncio
async def test_parallel_tool_use_with_judge_failure_event_ordering(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Verify the streaming event ordering is valid when the judge fails
    during parallel tool_use blocks."""
    mock_anthropic.enqueue(
        parallel_tool_response(
            [
                ("Bash", {"command": "echo hello"}),
                ("Read", {"file_path": "/tmp/a.txt"}),
                ("Write", {"file_path": "/tmp/b.txt", "content": "test"}),
            ]
        )
    )

    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config):
        session = ClaudeCodeSimulator(GATEWAY_URL, API_KEY)
        turn = await session.send("Do three things")

    # All 3 tool calls should pass through
    assert len(turn.tool_calls) == 3

    # Validate full streaming protocol compliance
    validate_anthropic_event_ordering(turn.raw_events).assert_valid()

    # stop_reason should be "tool_use" (tools were emitted)
    assert turn.stop_reason == "tool_use"
