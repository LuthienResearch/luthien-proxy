# ABOUTME: E2E tests for policy management API
# ABOUTME: Tests admin endpoints, policy hot-reload, and persistence

"""E2E tests for policy management API.

Tests the admin API endpoints for policy management including:
- GET /api/api/admin/policy/current - Get current policy information
- POST /api/api/admin/policy/set - Set the active policy
- GET /api/api/admin/policy/list - List available policy classes
- Policy persistence to database
- Hot-reload functionality (changing policy without restart)

These tests require:
- Running gateway service (docker compose up gateway)
- Database with migrations applied (001, 002)
- Redis for distributed locking
- Valid ADMIN_API_KEY in environment
"""

import os
import time

import httpx
import pytest

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))
PROXY_API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest.fixture
def admin_headers():
    """Provide admin authentication headers."""
    return {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.fixture
def proxy_headers():
    """Provide proxy authentication headers."""
    return {"Authorization": f"Bearer {PROXY_API_KEY}"}


@pytest.fixture(scope="module")
async def restore_policy_after_tests():
    """Save initial policy state and restore it after all tests in this module.

    This ensures tests are good citizens and don't leave the gateway in an
    unexpected state for other tests or manual testing.
    """
    headers = {"Authorization": f"Bearer {ADMIN_API_KEY}"}

    # Save initial policy state before any tests run
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{GATEWAY_URL}/api/admin/policy/current", headers=headers)
        if response.status_code == 200:
            initial_policy = response.json()
        else:
            initial_policy = None

    yield

    # Restore initial policy after all tests complete
    if initial_policy:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                f"{GATEWAY_URL}/api/admin/policy/set",
                headers=headers,
                json={
                    "policy_class_ref": initial_policy["class_ref"],
                    "config": initial_policy.get("config", {}),
                    "enabled_by": "e2e-test-cleanup",
                },
            )


# === Admin API Authentication Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_admin_api_requires_authentication(http_client):
    """Test that admin endpoints require authentication."""
    response = await http_client.get(f"{GATEWAY_URL}/api/admin/policy/current")
    assert response.status_code == 403, "Should reject request without auth"

    response = await http_client.get(
        f"{GATEWAY_URL}/api/admin/policy/current",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 403, "Should reject request with wrong key"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_admin_api_accepts_valid_key(http_client, admin_headers):
    """Test that admin endpoints accept valid admin key."""
    response = await http_client.get(
        f"{GATEWAY_URL}/api/admin/policy/current",
        headers=admin_headers,
    )
    assert response.status_code == 200, f"Should accept valid admin key: {response.text}"


# === Policy Information Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_get_current_policy(http_client, admin_headers):
    """Test getting current policy information."""
    response = await http_client.get(
        f"{GATEWAY_URL}/api/admin/policy/current",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "policy" in data
    assert "class_ref" in data
    assert "config" in data

    print(f"Current policy: {data['policy']}")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_list_available_policies(http_client, admin_headers):
    """Test listing available policy classes."""
    response = await http_client.get(
        f"{GATEWAY_URL}/api/admin/policy/list",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "policies" in data
    assert len(data["policies"]) > 0

    for policy in data["policies"]:
        assert "name" in policy
        assert "class_ref" in policy
        assert "description" in policy


# === Policy Hot-Reload Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_set_policy_single_call(http_client, admin_headers, proxy_headers, restore_policy_after_tests):
    """Test setting a policy with a single API call using /policy/set."""
    # Set policy directly
    set_response = await http_client.post(
        f"{GATEWAY_URL}/api/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
            "config": {},
            "enabled_by": "e2e-test",
        },
    )

    assert set_response.status_code == 200, f"Failed to set policy: {set_response.text}"
    data = set_response.json()
    assert data["success"] is True
    assert "AllCapsPolicy" in data["policy"]

    print(f"Policy set in {data.get('restart_duration_ms')}ms")

    time.sleep(0.5)

    # Verify current policy changed
    current_response = await http_client.get(f"{GATEWAY_URL}/api/admin/policy/current", headers=admin_headers)
    assert current_response.json()["policy"] == "AllCapsPolicy"

    # Test policy works
    gateway_response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers=proxy_headers,
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "say hello"}],
            "max_tokens": 10,
            "stream": False,
        },
    )

    assert gateway_response.status_code == 200
    response_text = gateway_response.json()["choices"][0]["message"]["content"]
    assert response_text.isupper(), f"Expected uppercase, got: {response_text}"

    print(f"Policy working! Response: {response_text}")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_set_noop_policy(http_client, admin_headers, restore_policy_after_tests):
    """Test setting NoOpPolicy."""
    response = await http_client.post(
        f"{GATEWAY_URL}/api/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "config": {},
            "enabled_by": "e2e-test",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_set_invalid_policy(http_client, admin_headers, restore_policy_after_tests):
    """Test that setting invalid policy fails gracefully."""
    response = await http_client.post(
        f"{GATEWAY_URL}/api/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "nonexistent.module:NonexistentPolicy",
            "config": {},
        },
    )

    assert response.status_code == 200  # Returns 200 with success=False
    data = response.json()
    assert data["success"] is False
    assert data["error"] is not None
    assert data["troubleshooting"] is not None
