"""Mock e2e tests for DogfoodSafetyPolicy.

Tests the fast pattern-matching policy that blocks self-destructive commands
(e.g. ``docker compose down``, ``pkill uvicorn``) from being executed by an
agent running through the proxy.  The policy works by intercepting tool_use
blocks in the Anthropic response and replacing any that match a dangerous
pattern with a text block containing a "BLOCKED" message.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_dogfood_safety.py -v
"""

import json

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}

_DOGFOOD_SAFETY = "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy"


# =============================================================================
# Dangerous commands are blocked
# =============================================================================


@pytest.mark.asyncio
async def test_dangerous_bash_command_is_blocked(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """A tool_use block with a dangerous docker command is replaced with a BLOCKED text block.

    The policy intercepts the ``Bash`` tool_use and replaces it with a text
    block whose content contains "BLOCKED".  The response type therefore
    changes from ``tool_use`` to ``text``.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "docker compose down"}))

    async with policy_context(_DOGFOOD_SAFETY, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    content = body["content"]
    assert len(content) > 0, "Response content should not be empty"
    first_block = content[0]
    assert first_block["type"] == "text", (
        f"Expected blocked tool_use to be replaced with text block, got: {first_block['type']!r}"
    )
    assert "BLOCKED" in first_block["text"], f"Expected BLOCKED message in text block, got: {first_block['text']!r}"


@pytest.mark.asyncio
async def test_streaming_dangerous_command_is_blocked(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Streaming variant: dangerous Bash command blocked via the streaming code path.

    The policy buffers ``input_json_delta`` chunks as they arrive and evaluates
    the assembled tool input on the stop event.  The client should receive a
    text delta with "BLOCKED" instead of the original tool_use stream.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "docker compose down"}))

    blocked_text = []
    async with policy_context(_DOGFOOD_SAFETY, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "POST",
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=auth_headers,
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            event = json.loads(line[len("data:") :].strip())
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                blocked_text.append(delta.get("text", ""))

    full_text = "".join(blocked_text)
    assert "BLOCKED" in full_text, f"Expected BLOCKED message in streaming response, got: {full_text!r}"


# =============================================================================
# Safe commands pass through unchanged
# =============================================================================


@pytest.mark.asyncio
async def test_safe_bash_command_passes_through(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """A tool_use block with a harmless bash command passes through unchanged.

    ``ls -la`` matches no blocked pattern, so the response content should
    remain a ``tool_use`` block with the original tool name and input.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "ls -la"}))

    async with policy_context(_DOGFOOD_SAFETY, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    content = body["content"]
    assert len(content) > 0, "Response content should not be empty"
    first_block = content[0]
    assert first_block["type"] == "tool_use", (
        f"Safe command should pass through as tool_use, got: {first_block['type']!r}"
    )
    assert first_block["name"] == "Bash", f"Tool name should be 'Bash', got: {first_block['name']!r}"
    tool_input = first_block.get("input", {})
    assert tool_input.get("command") == "ls -la", f"Tool input should be unchanged, got: {tool_input!r}"


@pytest.mark.asyncio
async def test_non_bash_tool_passes_through(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """A tool_use block for a non-bash tool passes through unchanged.

    ``read_file`` is not in the list of monitored tool names, so the policy
    should not inspect or modify the response regardless of its input.
    """
    mock_anthropic.enqueue(tool_response("read_file", {"path": "/tmp/test"}))

    async with policy_context(_DOGFOOD_SAFETY, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    content = body["content"]
    assert len(content) > 0, "Response content should not be empty"
    first_block = content[0]
    assert first_block["type"] == "tool_use", (
        f"Non-bash tool should pass through as tool_use, got: {first_block['type']!r}"
    )
    assert first_block["name"] == "read_file", f"Tool name should be 'read_file', got: {first_block['name']!r}"
    assert first_block.get("input", {}).get("path") == "/tmp/test", (
        f"Tool input should be unchanged, got: {first_block.get('input')!r}"
    )


# =============================================================================
# Custom blocked pattern
# =============================================================================


@pytest.mark.asyncio
async def test_custom_blocked_pattern(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """A custom blocked pattern is respected by the policy.

    Configuring ``blocked_patterns: ["rm.*important"]`` causes any Bash
    command matching that pattern (e.g. ``rm important.txt``) to be blocked,
    even though it does not match any default dangerous pattern.
    """
    mock_anthropic.enqueue(tool_response("Bash", {"command": "rm important.txt"}))

    custom_config = {
        "blocked_patterns": ["rm.*important"],
        "tool_names": ["Bash"],
    }
    async with policy_context(_DOGFOOD_SAFETY, custom_config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    content = body["content"]
    assert len(content) > 0, "Response content should not be empty"
    first_block = content[0]
    assert first_block["type"] == "text", (
        f"Custom pattern match should produce a blocked text block, got: {first_block['type']!r}"
    )
    assert "BLOCKED" in first_block["text"], (
        f"Expected BLOCKED message for custom pattern, got: {first_block['text']!r}"
    )
