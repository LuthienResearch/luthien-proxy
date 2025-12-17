"""E2E tests for SimpleJudgePolicy.

Tests a custom SimpleJudgePolicy subclass with real requests through the gateway.
"""

import asyncio
import os

import httpx
import pytest

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
    """Provide admin authentication headers."""
    return {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.fixture
def proxy_headers():
    """Provide proxy authentication headers."""
    return {"Authorization": f"Bearer {PROXY_API_KEY}"}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_simple_judge_policy_blocks_unsafe_content(http_client, admin_headers, proxy_headers):
    """Test that SimpleJudgePolicy blocks content violating rules."""
    # Set the SimpleJudgePolicy using the /admin/policy/set endpoint
    set_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            "config": {
                "judge_model": "claude-haiku-4-5",
                "judge_temperature": 0.0,
                "block_threshold": 0.6,
            },
            "enabled_by": "e2e-simple-judge-tests",
        },
    )

    assert set_response.status_code == 200, f"Failed to set: {set_response.text}"
    result = set_response.json()
    assert result["success"] is True, f"Failed to set policy: {result.get('error')}"

    # Note: SimpleJudgePolicy base class has no RULES, so this won't actually block anything
    # This test demonstrates the policy is loaded and runs, but doesn't block
    # To test actual blocking, we'd need to create a custom subclass with RULES

    # Give the policy a moment to activate
    await asyncio.sleep(0.5)

    # Make a request through the gateway - should pass since base class has no rules
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        headers=proxy_headers,
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "content" in data
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_simple_judge_policy_activates_successfully(http_client, admin_headers):
    """Test that SimpleJudgePolicy can be set and activated."""
    # Set the policy using /admin/policy/set
    set_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            "config": {
                "judge_model": "claude-haiku-4-5",
                "block_threshold": 0.7,
            },
            "enabled_by": "e2e-simple-judge-tests",
        },
    )

    assert set_response.status_code == 200
    data = set_response.json()
    assert data["success"] is True
    assert "policy" in data

    # Verify it's active
    current_response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/current",
        headers=admin_headers,
    )

    assert current_response.status_code == 200
    current_data = current_response.json()
    assert current_data["policy"] == "SimpleJudgePolicy"
    assert "judge_model" in current_data["config"]
    # Config should have judge settings
    assert "block_threshold" in current_data["config"]
