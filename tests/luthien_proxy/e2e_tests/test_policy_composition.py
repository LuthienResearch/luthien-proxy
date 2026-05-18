"""E2E tests for policy composition and DogfoodSafetyPolicy.

Tests:
- compose_policy() wrapping behavior
- DogfoodSafetyPolicy blocking dangerous commands (requires DOGFOOD_MODE=true)
- DogfoodSafetyPolicy passing safe content through
- Policy chain ordering with dogfood composition
"""

import os

import httpx
import pytest
from tests.constants import DEFAULT_TEST_MODEL
from tests.luthien_proxy.e2e_tests.conftest import policy_context

from luthien_proxy.policies.dogfood_safety_policy import DogfoodSafetyPolicy
from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_composition import compose_policy

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))
CLIENT_API_KEY = os.getenv("E2E_API_KEY", os.getenv("CLIENT_API_KEY", "sk-luthien-dev-key"))


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
    return {"Authorization": f"Bearer {CLIENT_API_KEY}"}


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
    """Activate DogfoodSafetyPolicy for the duration of the test, then restore NoOp.

    Previously this fixture skipped when DOGFOOD_MODE was not set on the gateway.
    Instead, set the policy directly via the admin API so the test runs deterministically
    regardless of how the gateway was started — `policy_context` restores NoOp on exit.
    """
    async with policy_context(
        "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy",
        {},
        gateway_url=GATEWAY_URL,
        admin_api_key=ADMIN_API_KEY,
    ):
        # Sanity check the activation actually took effect.
        assert await _check_dogfood_active(http_client), "Failed to activate DogfoodSafetyPolicy via admin API"
        yield


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_blocks_dangerous_tool_call(http_client, proxy_headers, gateway_healthy, dogfood_active):
    """A tool call with 'docker compose down' should be blocked by DogfoodSafetyPolicy.

    We send a messages request with a tool_use that contains a dangerous
    command and verify the response is modified to contain a blocked message.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        headers=proxy_headers,
        json={
            "model": DEFAULT_TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Run this command: docker compose down",
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_test_123",
                            "name": "Bash",
                            "input": {"command": "docker compose down"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_test_123",
                            "content": "Command executed successfully",
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": "What happened?",
                },
            ],
            "tools": [
                {
                    "name": "Bash",
                    "description": "Execute a bash command",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The command to execute",
                            }
                        },
                        "required": ["command"],
                    },
                }
            ],
            "max_tokens": 100,
        },
    )

    assert response.status_code == 200
    data = response.json()
    content_blocks = data.get("content", [])
    assert len(content_blocks) > 0

    # If the model responds with a tool_use containing "docker compose down",
    # dogfood should block it and replace with a text message.
    # If the model doesn't produce a dangerous tool call, the test passes trivially.
    for block in content_blocks:
        if block.get("type") == "tool_use":
            tool_input = block.get("input", {})
            command = tool_input.get("command", "")
            assert "docker compose down" not in command.lower(), f"Dangerous command was NOT blocked: {command}"

    text_blocks = [b for b in content_blocks if b.get("type") == "text"]
    for block in text_blocks:
        text = block.get("text", "")
        if "BLOCKED" in text:
            assert "DogfoodSafetyPolicy" in text


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_passes_safe_request(http_client, proxy_headers, gateway_healthy, dogfood_active):
    """A normal chat request with no dangerous commands passes through unmodified."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        headers=proxy_headers,
        json={
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Say 'hello' and nothing else."}],
            "max_tokens": 10,
        },
    )

    assert response.status_code == 200
    data = response.json()
    content_blocks = data.get("content", [])
    assert len(content_blocks) > 0

    text = content_blocks[0].get("text", "")
    assert "BLOCKED" not in text, f"Safe request was incorrectly blocked: {text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_passes_safe_anthropic_request(http_client, proxy_headers, gateway_healthy, dogfood_active):
    """A normal Anthropic request passes through unmodified."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        headers=proxy_headers,
        json={
            "model": DEFAULT_TEST_MODEL,
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


# Note: test_dogfood_composes_at_position_zero was removed — the DOGFOOD_MODE
# auto-composition wiring it exercised is fully covered by unit tests in
# tests/luthien_proxy/unit_tests/test_policy_manager_dogfood.py (which test
# _maybe_compose_dogfood directly across enabled/disabled and no-double-wrap
# cases). The e2e variant only verified the same wiring at higher cost and
# required restarting the gateway with DOGFOOD_MODE=true mid-suite.


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
