"""Mock e2e tests for SimpleLLMPolicy on_error behavior.

Tests the fail-secure behavior: when the judge LLM is unavailable,
on_error='block' rejects content and on_error='pass' allows content
through with an injected warning notification.

Requires:
  - Gateway running with mock backend on a reachable port
  - Mock Anthropic server on port 18888

Run:
    E2E_GATEWAY_URL=http://localhost:8001 \
    E2E_API_KEY=sk-luthien-dev-key \
    E2E_ADMIN_API_KEY=admin-dev-key \
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_simple_llm_policy.py -v
"""

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response, tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator

pytestmark = pytest.mark.mock_e2e

_SIMPLE_LLM_POLICY = "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"

# Judge pointed at an unreachable URL to force failure
_UNREACHABLE_JUDGE = {
    "instructions": "Block all content",
    "model": "claude-haiku-4-5",
    "api_base": "http://127.0.0.1:19999",
    "api_key": "fake-key",
}

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}


# =============================================================================
# on_error='block' (fail-secure): judge failure → content blocked
# =============================================================================


@pytest.mark.asyncio
async def test_judge_failure_blocks_text_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """When judge is unreachable and on_error='block', streaming text is blocked.

    The client should receive an empty response (no text content) because
    the policy rejects content when the judge can't evaluate it.
    """
    mock_anthropic.enqueue(text_response("This should be blocked"))
    config = {**_UNREACHABLE_JUDGE, "on_error": "block"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn = await session.send("Say hello")

    assert turn.text == "", f"Expected empty text when judge fails with on_error='block', got: {turn.text!r}"


@pytest.mark.asyncio
async def test_judge_failure_blocks_tool_use_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """When judge is unreachable and on_error='block', streaming tool_use is blocked.

    The tool call should be silently dropped.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "echo hello"}))
    config = {**_UNREACHABLE_JUDGE, "on_error": "block"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn = await session.send("Run echo hello")

    assert len(turn.tool_calls) == 0, (
        f"Expected no tool calls when judge fails with on_error='block', got: {turn.tool_calls}"
    )


# =============================================================================
# on_error='pass' (fail-open with warning): judge failure → content + warning
# =============================================================================


@pytest.mark.asyncio
async def test_judge_failure_passes_text_with_warning_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """When judge is unreachable and on_error='pass', streaming text passes through
    and a warning notification is injected.
    """
    mock_anthropic.enqueue(text_response("Hello from model"))
    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn = await session.send("Say hello")

    assert "Hello from model" in turn.text, f"Expected original text to pass through, got: {turn.text!r}"
    assert "Safety judge unavailable" in turn.text, f"Expected warning about unavailable judge, got: {turn.text!r}"


@pytest.mark.asyncio
async def test_judge_failure_passes_tool_use_with_warning_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """When judge is unreachable and on_error='pass', streaming tool_use passes through
    and a warning notification is injected as a text block.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "echo hello"}))
    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn = await session.send("Run echo hello")

    assert len(turn.tool_calls) == 1, f"Expected tool call to pass through, got: {turn.tool_calls}"
    assert turn.tool_calls[0].name == "Bash"
    assert turn.tool_calls[0].input == {"command": "echo hello"}
    assert "Safety judge unavailable" in turn.text, (
        f"Expected warning about unavailable judge in text, got: {turn.text!r}"
    )


# =============================================================================
# Non-streaming variants
# =============================================================================


@pytest.mark.asyncio
async def test_judge_failure_blocks_text_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Non-streaming: judge failure with on_error='block' produces empty content."""
    mock_anthropic.enqueue(text_response("This should be blocked"))
    config = {**_UNREACHABLE_JUDGE, "on_error": "block"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    content = body.get("content", [])
    text_blocks = [b for b in content if b.get("type") == "text" and b.get("text", "").strip()]
    assert len(text_blocks) == 0, f"Expected no text content when blocked, got: {content}"


@pytest.mark.asyncio
async def test_judge_failure_passes_text_with_warning_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Non-streaming: judge failure with on_error='pass' returns original content + warning."""
    mock_anthropic.enqueue(text_response("Hello from model"))
    config = {**_UNREACHABLE_JUDGE, "on_error": "pass"}

    async with policy_context(_SIMPLE_LLM_POLICY, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    content = body.get("content", [])
    all_text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    assert "Hello from model" in all_text, f"Expected original text, got: {all_text!r}"
    assert "Safety judge unavailable" in all_text, f"Expected warning, got: {all_text!r}"
