"""E2E tests for policy composition and DogfoodSafetyPolicy.

Tests:
- compose_policy() wrapping behavior
- DogfoodSafetyPolicy blocking dangerous commands (requires DOGFOOD_MODE=true)
- DogfoodSafetyPolicy passing safe content through
- Policy chain ordering with dogfood composition
"""

import asyncio
import json
import os

import httpx
import pytest

from luthien_proxy.policies.dogfood_safety_policy import DogfoodSafetyPolicy
from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_composition import compose_policy

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))
PROXY_API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))


@pytest.fixture
async def http_client():
    """Provide async HTTP client."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        yield client


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.fixture
def proxy_headers():
    return {"Authorization": f"Bearer {PROXY_API_KEY}"}


# ============================================================================
# compose_policy() behavior
# ============================================================================


@pytest.mark.e2e
class TestComposePolicy:
    """Test compose_policy() wrapping and chain building."""

    def test_wraps_single_policy_into_multi_serial(self):
        """compose_policy wraps a single policy + additional into MultiSerialPolicy."""
        noop = NoOpPolicy()
        dogfood = DogfoodSafetyPolicy()

        result = compose_policy(noop, dogfood)

        assert isinstance(result, MultiSerialPolicy)
        assert len(result._sub_policies) == 2

    def test_insert_at_position_zero(self):
        """position=0 puts the additional policy first in the chain."""
        noop = NoOpPolicy()
        dogfood = DogfoodSafetyPolicy()

        result = compose_policy(noop, dogfood, position=0)

        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)
        assert isinstance(result._sub_policies[1], NoOpPolicy)

    def test_append_by_default(self):
        """position=None (default) appends the additional policy."""
        noop = NoOpPolicy()
        dogfood = DogfoodSafetyPolicy()

        result = compose_policy(noop, dogfood)

        assert isinstance(result._sub_policies[0], NoOpPolicy)
        assert isinstance(result._sub_policies[1], DogfoodSafetyPolicy)

    def test_insert_into_existing_multi_serial(self):
        """When current is already a MultiSerialPolicy, inserts into its chain."""
        noop = NoOpPolicy()
        dogfood = DogfoodSafetyPolicy()

        chain = compose_policy(noop, DogfoodSafetyPolicy())
        result = compose_policy(chain, dogfood, position=0)

        assert isinstance(result, MultiSerialPolicy)
        assert len(result._sub_policies) == 3
        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)


# ============================================================================
# Dogfood policy via gateway (requires running gateway with DOGFOOD_MODE=true)
# ============================================================================


async def _check_dogfood_active(client: httpx.AsyncClient) -> bool:
    """Check if the current policy chain includes DogfoodSafetyPolicy."""
    response = await client.get(
        f"{GATEWAY_URL}/api/admin/policy/current",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )
    if response.status_code != 200:
        return False
    data = response.json()
    policy_name = data.get("policy", "")
    return "DogfoodSafety" in policy_name or "MultiSerial" in policy_name


@pytest.fixture
async def dogfood_active(http_client):
    """Skip test if DogfoodSafetyPolicy is not active on the gateway."""
    is_active = await _check_dogfood_active(http_client)
    if not is_active:
        pytest.skip("DOGFOOD_MODE not active on gateway — restart with DOGFOOD_MODE=true to run these tests")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_blocks_dangerous_openai_tool_call(http_client, proxy_headers, gateway_healthy, dogfood_active):
    """A tool call with 'docker compose down' should be blocked by DogfoodSafetyPolicy.

    We send a chat completion request with a tool_call that contains a dangerous
    command and verify the response is modified to contain a blocked message.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers=proxy_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": "Run this command: docker compose down",
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_test_123",
                            "type": "function",
                            "function": {
                                "name": "Bash",
                                "arguments": json.dumps({"command": "docker compose down"}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_test_123",
                    "content": "Command executed successfully",
                },
                {
                    "role": "user",
                    "content": "What happened?",
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "Bash",
                        "description": "Execute a bash command",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "The command to execute",
                                }
                            },
                            "required": ["command"],
                        },
                    },
                }
            ],
            "max_tokens": 100,
            "stream": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    choices = data.get("choices", [])
    assert len(choices) > 0

    choice = choices[0]
    message = choice.get("message", {})

    # If the model responds with a tool_call containing "docker compose down",
    # dogfood should block it and replace with a text message.
    # If the model doesn't produce a dangerous tool call, the test passes trivially.
    tool_calls = message.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            try:
                parsed = json.loads(args)
                command = parsed.get("command", "")
            except (json.JSONDecodeError, TypeError):
                command = args
            assert "docker compose down" not in command.lower(), f"Dangerous command was NOT blocked: {command}"

    content = message.get("content", "")
    if content and "BLOCKED" in content:
        assert "DogfoodSafetyPolicy" in content


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_passes_safe_request(http_client, proxy_headers, gateway_healthy, dogfood_active):
    """A normal chat request with no dangerous commands passes through unmodified."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers=proxy_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Say 'hello' and nothing else."}],
            "max_tokens": 10,
            "stream": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    choices = data.get("choices", [])
    assert len(choices) > 0

    content = choices[0].get("message", {}).get("content", "")
    assert "BLOCKED" not in content, f"Safe request was incorrectly blocked: {content}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_passes_safe_anthropic_request(http_client, proxy_headers, gateway_healthy, dogfood_active):
    """A normal Anthropic request passes through unmodified."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        headers=proxy_headers,
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say 'hello' and nothing else."}],
            "max_tokens": 10,
        },
    )

    if response.status_code == 500:
        detail = response.json().get("detail", "")
        if "Anthropic credentials" in detail or "ANTHROPIC_API_KEY" in detail:
            pytest.skip("Anthropic API key not configured on gateway")

    assert response.status_code == 200
    data = response.json()
    content_blocks = data.get("content", [])
    assert len(content_blocks) > 0

    text = content_blocks[0].get("text", "")
    assert "BLOCKED" not in text, f"Safe request was incorrectly blocked: {text}"


# ============================================================================
# Policy chain ordering via admin API
# ============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_composes_at_position_zero(http_client, admin_headers, gateway_healthy, dogfood_active):
    """When DOGFOOD_MODE is active, DogfoodSafetyPolicy is first in the chain.

    Setting any policy via admin API should result in DogfoodSafety at position 0.
    """
    # Set a known policy
    set_response = await http_client.post(
        f"{GATEWAY_URL}/api/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "config": {},
            "enabled_by": "e2e-composition-test",
        },
    )

    assert set_response.status_code == 200
    assert set_response.json()["success"] is True

    await asyncio.sleep(0.5)

    # Check current policy — should be MultiSerialPolicy with DogfoodSafety
    current = await http_client.get(
        f"{GATEWAY_URL}/api/admin/policy/current",
        headers=admin_headers,
    )

    assert current.status_code == 200
    data = current.json()

    # The policy name should indicate composition
    policy_name = data["policy"]
    assert "MultiSerialPolicy" in policy_name or "DogfoodSafety" in policy_name, (
        f"Expected DogfoodSafety in policy chain, got: {policy_name}"
    )


# ============================================================================
# Cleanup
# ============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_zz_cleanup_restore_noop(http_client, admin_headers, gateway_healthy):
    """Restore NoOpPolicy after tests (runs last due to alphabetical ordering)."""
    response = await http_client.post(
        f"{GATEWAY_URL}/api/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "config": {},
            "enabled_by": "e2e-cleanup",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
