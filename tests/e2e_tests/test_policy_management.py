# ABOUTME: E2E tests for policy management API
# ABOUTME: Tests admin endpoints, policy hot-reload, and persistence

"""E2E tests for policy management API.

Tests the admin API endpoints for policy management including:
- GET /admin/policy/current - Get current policy information
- POST /admin/policy/create - Create a named policy instance
- POST /admin/policy/activate - Activate a saved policy instance
- GET /admin/policy/list - List available policy classes
- GET /admin/policy/instances - List saved policy instances
- GET /admin/policy/source-info - Get configuration details
- Policy persistence across different POLICY_SOURCE modes
- Hot-reload functionality (changing policy without restart)

These tests require:
- Running v2-gateway service (docker compose up gateway)
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


# === Admin API Authentication Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_admin_api_requires_authentication(http_client):
    """Test that admin endpoints require authentication."""
    response = await http_client.get(f"{GATEWAY_URL}/admin/policy/current")
    assert response.status_code == 403, "Should reject request without auth"

    response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/current",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 403, "Should reject request with wrong key"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_admin_api_accepts_valid_key(http_client, admin_headers):
    """Test that admin endpoints accept valid admin key."""
    response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/current",
        headers=admin_headers,
    )
    assert response.status_code == 200, f"Should accept valid admin key: {response.text}"


# === Policy Information Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_get_current_policy(http_client, admin_headers):
    """Test getting current policy information."""
    response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/current",
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
async def test_get_policy_source_info(http_client, admin_headers):
    """Test getting policy source configuration."""
    response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/source-info",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "policy_source" in data
    assert "yaml_path" in data
    assert "supports_runtime_changes" in data

    print(f"Policy source: {data['policy_source']}")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_list_available_policies(http_client, admin_headers):
    """Test listing available policy classes."""
    response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/list",
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


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_list_policy_instances(http_client, admin_headers):
    """Test listing saved policy instances."""
    response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/instances",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "instances" in data


# === Policy Hot-Reload Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_create_and_activate_policy(http_client, admin_headers, proxy_headers):
    """Test creating and activating AllCapsPolicy and verifying it works."""
    instance_name = f"test-allcaps-{int(time.time())}"

    # Create policy instance
    create_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/create",
        headers=admin_headers,
        json={
            "name": instance_name,
            "policy_class_ref": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
            "config": {},
            "created_by": "e2e-test",
        },
    )

    assert create_response.status_code == 200, f"Failed to create: {create_response.text}"
    assert create_response.json()["success"] is True

    # Activate policy instance
    activate_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/activate",
        headers=admin_headers,
        json={
            "name": instance_name,
            "activated_by": "e2e-test",
        },
    )

    assert activate_response.status_code == 200, f"Failed to activate: {activate_response.text}"
    data = activate_response.json()
    assert data["success"] is True

    print(f"Policy activated in {data.get('restart_duration_ms')}ms")

    time.sleep(0.5)

    # Verify current policy changed
    current_response = await http_client.get(f"{GATEWAY_URL}/admin/policy/current", headers=admin_headers)
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
async def test_activate_noop_policy(http_client, admin_headers):
    """Test activating NoOpPolicy."""
    instance_name = f"test-noop-{int(time.time())}"

    await http_client.post(
        f"{GATEWAY_URL}/admin/policy/create",
        headers=admin_headers,
        json={
            "name": instance_name,
            "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "config": {},
        },
    )

    response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/activate",
        headers=admin_headers,
        json={"name": instance_name},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_create_invalid_policy(http_client, admin_headers):
    """Test that creating invalid policy fails."""
    response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/create",
        headers=admin_headers,
        json={
            "name": "invalid",
            "policy_class_ref": "nonexistent.module:NonexistentPolicy",
            "config": {},
        },
    )

    assert response.status_code == 500


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_activate_nonexistent_policy(http_client, admin_headers):
    """Test that activating non-existent policy fails."""
    response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/activate",
        headers=admin_headers,
        json={"name": "does-not-exist"},
    )

    assert response.status_code == 404
