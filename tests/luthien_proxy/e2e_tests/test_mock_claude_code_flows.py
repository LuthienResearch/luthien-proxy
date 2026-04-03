"""PoC flow tests using ClaudeCodeSimulator.

Four tests that prove the full gateway pipeline fires with realistic
Claude Code-style requests (system blocks, tool definitions, cache_control,
stream=True, multi-turn history).

These are not exhaustive — they establish that the approach works.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_claude_code_flows.py -v
"""

import os

import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response, tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator

AUTH_MODE = os.getenv("AUTH_MODE", "both")

pytestmark = pytest.mark.mock_e2e


@pytest.mark.asyncio
async def test_realistic_request_reaches_backend(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
):
    """Full chain fires: simulator → gateway (auth + pipeline) → mock backend.

    Asserts both sides:
    - Response came back correctly (gateway → simulator)
    - Mock received a realistic Claude Code-shaped request (gateway → mock)
    """
    mock_anthropic.enqueue(text_response("Hello from the gateway"))
    session = ClaudeCodeSimulator(gateway_url, api_key)

    turn = await session.send("Hello")

    assert turn.text == "Hello from the gateway"

    req = mock_anthropic.last_request()
    assert req is not None, "Gateway never forwarded the request to the backend"
    assert isinstance(req.get("system"), list), "system must be a blocks array"
    assert req["system"][0].get("cache_control") == {"type": "ephemeral"}
    assert all("input_schema" in t for t in req.get("tools", []))
    assert req.get("stream") is True


@pytest.mark.asyncio
async def test_bad_auth_rejected_before_backend(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
):
    """Gateway auth: bad key gets 401 (proxy_key mode) or passes through (both mode)."""
    mock_anthropic.enqueue(text_response("passthrough or blocked"))
    session = ClaudeCodeSimulator(gateway_url, api_key="sk-bad-key")

    if AUTH_MODE == "both":
        turn = await session.send("hello")
        assert turn.text == "passthrough or blocked"
        assert mock_anthropic.last_request() is not None, "Backend should receive passthrough request"
    else:
        import httpx

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await session.send("hello")

        assert exc.value.response.status_code == 401
        assert mock_anthropic.last_request() is None, "Backend should not receive request with bad auth"


@pytest.mark.asyncio
async def test_single_tool_use_loop(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
):
    """Complete one tool use cycle with a realistic request shape.

    user → model calls Bash → provide result → model responds with text.
    Asserts the message history is built correctly for the second request.
    """
    session = ClaudeCodeSimulator(gateway_url, api_key)

    mock_anthropic.enqueue(tool_response("Bash", {"command": "echo hello"}))
    turn1 = await session.send("Run echo hello")

    assert turn1.stop_reason == "tool_use"
    assert turn1.tool_calls[0].name == "Bash"
    assert turn1.tool_calls[0].input == {"command": "echo hello"}

    mock_anthropic.enqueue(text_response("The output was: hello"))
    turn2 = await session.continue_with_tool_result(turn1.tool_calls[0].id, "hello\n")

    assert turn2.stop_reason == "end_turn"
    assert "hello" in turn2.text

    # Second request carried the full accumulated history
    req = mock_anthropic.last_request()
    assert len(req["messages"]) == 3  # user, assistant(tool_use), user(tool_result)
    assert any(b["type"] == "tool_use" for b in req["messages"][1]["content"])
    assert any(b["type"] == "tool_result" for b in req["messages"][2]["content"])


@pytest.mark.asyncio
async def test_policy_pipeline_fires_on_realistic_request(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Policy pipeline runs on realistic Claude Code-shaped streaming requests.

    If the pipeline short-circuited, AllCapsPolicy would not uppercase the response.
    """
    mock_anthropic.enqueue(text_response("hello world"))
    session = ClaudeCodeSimulator(gateway_url, api_key)

    async with policy_context(
        "luthien_proxy.policies.all_caps_policy:AllCapsPolicy", {}, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        turn = await session.send("Say hello")

    assert turn.text == "HELLO WORLD", f"Policy pipeline did not run — got: {turn.text!r}"
