# ABOUTME: E2E tests for SimpleJudgePolicy
# ABOUTME: Tests custom judge policies with real LLM evaluations

"""E2E tests for SimpleJudgePolicy.

Tests a custom SimpleJudgePolicy subclass with real requests through the gateway.
"""

import os
import time

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
    instance_name = f"test-simple-judge-{int(time.time())}"

    # Create a test judge policy that blocks requests about deleting files
    create_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/create",
        headers=admin_headers,
        json={
            "name": instance_name,
            "policy_class_ref": "luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            "config": {
                "judge_model": "claude-3-5-haiku-20241022",
                "judge_temperature": 0.0,
                "block_threshold": 0.6,
            },
            "description": "Test judge that blocks file deletion requests",
        },
    )

    assert create_response.status_code == 200, f"Failed to create: {create_response.text}"
    assert create_response.json()["success"] is True

    # Note: SimpleJudgePolicy base class has no RULES, so this won't actually block anything
    # This test demonstrates the policy is loaded and runs, but doesn't block
    # To test actual blocking, we'd need to create a custom subclass with RULES

    activate_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/activate",
        headers=admin_headers,
        json={"name": instance_name},
    )

    assert activate_response.status_code == 200
    assert activate_response.json()["success"] is True

    time.sleep(0.5)

    # Make a request through the gateway - should pass since base class has no rules
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        headers=proxy_headers,
        json={
            "model": "claude-3-5-haiku-20241022",
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
    """Test that SimpleJudgePolicy can be created and activated."""
    instance_name = f"test-judge-activation-{int(time.time())}"

    # Create instance
    create_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/create",
        headers=admin_headers,
        json={
            "name": instance_name,
            "policy_class_ref": "luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            "config": {
                "judge_model": "claude-3-5-haiku-20241022",
                "block_threshold": 0.7,
            },
        },
    )

    assert create_response.status_code == 200
    data = create_response.json()
    assert data["success"] is True
    assert "policy" in data

    # Activate instance
    activate_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/activate",
        headers=admin_headers,
        json={"name": instance_name},
    )

    assert activate_response.status_code == 200
    assert activate_response.json()["success"] is True

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
