"""E2E tests: single tool_use with judge failure (on_error=pass).

Scenario:
  1. Backend responds with a single Bash tool_use block
  2. Judge fails (unreachable URL) → warning injected, tool passes through (on_error=pass)
  3. Client sends tool_result
  4. Backend responds with text
  5. Session must remain functional (no 400)

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_simple_llm_single_tool.py -v
"""

import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response, tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator
from tests.luthien_proxy.fixtures.anthropic_stream_validator import validate_anthropic_event_ordering

pytestmark = pytest.mark.mock_e2e

_SIMPLE_LLM_POLICY = "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"

_UNREACHABLE_JUDGE = {
    "instructions": "Block all content",
    "model": "claude-haiku-4-5",
    "api_base": "http://127.0.0.1:19999",
    "auth_provider": "user_credentials",
}


@pytest.mark.asyncio
async def test_single_tool_use_with_judge_failure_session_survives(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Single tool + judge failure (on_error=pass): turn 2 with tool_result must succeed."""
    mock_anthropic.enqueue(tool_response("Bash", {"command": "ls"}))
    mock_anthropic.enqueue(text_response("ls output: file1.txt file2.txt"))

    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn1 = await session.send("List the files")

        assert len(turn1.tool_calls) == 1, f"Expected 1 tool call, got {turn1.tool_calls}"
        assert turn1.stop_reason == "tool_use"
        assert "Safety judge unavailable" in turn1.text

        turn2 = await session.continue_with_tool_result(turn1.tool_calls[0].id, "file1.txt\nfile2.txt")

        assert "file" in turn2.text.lower(), f"Expected tool result response, got: {turn2.text!r}"


@pytest.mark.asyncio
async def test_single_tool_use_with_judge_failure_event_ordering(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Streaming event ordering must be valid when judge fails on a single tool_use."""
    mock_anthropic.enqueue(tool_response("Bash", {"command": "echo hello"}))

    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn = await session.send("Echo hello")

    assert len(turn.tool_calls) == 1
    validate_anthropic_event_ordering(turn.raw_events).assert_valid()
    assert turn.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_yash_scrapling_install_scenario_session_survives(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Regression test for issue #708: Yash's 'install Scrapling' scenario.

    Customer: Yash (RealPage), 2026-05-06 live trial.
    Symptom: 'safety judgment unavailable — API 400' after judge failure on tool call.

    Scenario: Claude attempts `pip install scrapling` (a Bash tool call). The judge
    fails (unreachable URL, simulating API 400 due to concurrency issues as observed
    in the trial). The session must survive — turn 2 (the follow-up with the tool
    result) must succeed without a 400 error, and streaming event ordering must be
    valid throughout.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "pip install scrapling"}))
    mock_anthropic.enqueue(text_response("Scrapling has been set up. Ready to use."))

    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn1 = await session.send("install Scrapling")

        assert len(turn1.tool_calls) == 1, f"Expected 1 tool call, got {turn1.tool_calls}"
        assert turn1.stop_reason == "tool_use"
        assert "Safety judge unavailable" in turn1.text
        validate_anthropic_event_ordering(turn1.raw_events).assert_valid()

        turn2 = await session.continue_with_tool_result(
            turn1.tool_calls[0].id,
            "Successfully installed scrapling-0.4.0",
        )

        assert turn2.text, f"Turn 2 must return a non-empty response, got: {turn2.text!r}"
