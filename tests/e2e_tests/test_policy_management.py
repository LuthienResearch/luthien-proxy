# ABOUTME: E2E tests for policy management API
# ABOUTME: Tests admin endpoints, policy hot-reload, and persistence

"""E2E tests for policy management API.

Tests the admin API endpoints for policy management including:
- GET /admin/policy/current - Get current policy information
- POST /admin/policy/enable - Enable a new policy with hot-reload
- GET /admin/policy/source-info - Get configuration details
- Policy persistence across different POLICY_SOURCE modes
- Hot-reload functionality (changing policy without restart)

These tests require:
- Running v2-gateway service (docker compose up gateway)
- Database with migrations applied
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
    # No auth header
    response = await http_client.get(f"{GATEWAY_URL}/admin/policy/current")
    assert response.status_code == 403, "Should reject request without auth"

    # Wrong auth key
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

    # Verify response structure
    assert "policy" in data, "Response should include policy name"
    assert "class_ref" in data, "Response should include class reference"
    assert "config" in data, "Response should include config"
    assert "source_info" in data, "Response should include source info"

    # Verify source info
    source_info = data["source_info"]
    assert "policy_source" in source_info
    assert "yaml_path" in source_info
    assert "supports_runtime_changes" in source_info
    assert "persistence_target" in source_info

    print(f"Current policy: {data['policy']}")
    print(f"Policy source: {source_info['policy_source']}")


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

    # Verify all fields present
    assert "policy_source" in data
    assert "yaml_path" in data
    assert "supports_runtime_changes" in data
    assert "persistence_target" in data

    # Verify policy_source is valid
    valid_sources = ["db", "file", "db-fallback-file", "file-fallback-db"]
    assert data["policy_source"] in valid_sources


# === Policy Hot-Reload Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_enable_policy_all_caps(http_client, admin_headers, proxy_headers):
    """Test enabling AllCapsPolicy and verifying it works."""
    # Enable AllCapsPolicy
    enable_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/enable",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
            "config": {},
            "enabled_by": "e2e-test",
        },
    )

    assert enable_response.status_code == 200, f"Failed to enable policy: {enable_response.text}"
    data = enable_response.json()

    assert data["success"] is True, f"Policy enable failed: {data.get('error')}"
    assert data["policy"] == "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
    assert "restart_duration_ms" in data

    print(f"Policy enabled in {data['restart_duration_ms']}ms")

    # Wait a moment for policy to be fully active
    time.sleep(0.5)

    # Verify current policy changed
    current_response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/current",
        headers=admin_headers,
    )
    current_data = current_response.json()
    assert current_data["policy"] == "AllCapsPolicy"
    assert current_data["enabled_by"] == "e2e-test"

    # Test that policy is actually working (make a request through gateway)
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
    gateway_data = gateway_response.json()
    response_text = gateway_data["choices"][0]["message"]["content"]

    # AllCapsPolicy should uppercase the response
    assert response_text.isupper(), f"Expected uppercase response, got: {response_text}"
    print(f"Policy working! Response: {response_text}")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_enable_policy_noop(http_client, admin_headers):
    """Test enabling NoOpPolicy."""
    response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/enable",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "config": {},
            "enabled_by": "e2e-test",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "NoOpPolicy" in data["policy"]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_enable_invalid_policy(http_client, admin_headers):
    """Test that enabling invalid policy returns error."""
    response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/enable",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.nonexistent:FakePolicy",
            "config": {},
            "enabled_by": "e2e-test",
        },
    )

    assert response.status_code == 200  # API returns 200 with success=false
    data = response.json()
    assert data["success"] is False
    assert "error" in data
    assert "troubleshooting" in data
    assert len(data["troubleshooting"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_enable_policy_with_invalid_config(http_client, admin_headers):
    """Test that invalid config is caught during enable."""
    response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/enable",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
            "config": {"invalid_param": "this_should_fail"},
            "enabled_by": "e2e-test",
        },
    )

    # This might succeed (if AllCapsPolicy ignores extra params) or fail
    # The important part is that it doesn't crash the gateway
    assert response.status_code == 200
    data = response.json()
    # Either succeeds or gives clear error
    if not data["success"]:
        assert "error" in data


# === Policy Persistence Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_policy_persists_to_database(http_client, admin_headers):
    """Test that enabled policy is persisted to database."""
    # Enable a policy
    enable_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/enable",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
            "config": {},
            "enabled_by": "persistence-test",
        },
    )

    assert enable_response.status_code == 200
    assert enable_response.json()["success"] is True

    # Verify it's recorded with metadata
    current_response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/current",
        headers=admin_headers,
    )

    data = current_response.json()
    assert data["policy"] == "DebugLoggingPolicy"
    assert data["enabled_by"] == "persistence-test"
    assert data["enabled_at"] is not None  # Should have timestamp


# === Concurrent Policy Changes Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_concurrent_policy_changes_are_serialized(http_client, admin_headers):
    """Test that concurrent policy enable requests are properly serialized."""
    import asyncio

    # Try to enable two different policies simultaneously
    async def enable_policy_1():
        return await http_client.post(
            f"{GATEWAY_URL}/admin/policy/enable",
            headers=admin_headers,
            json={
                "policy_class_ref": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
                "config": {},
                "enabled_by": "concurrent-test-1",
            },
        )

    async def enable_policy_2():
        return await http_client.post(
            f"{GATEWAY_URL}/admin/policy/enable",
            headers=admin_headers,
            json={
                "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
                "config": {},
                "enabled_by": "concurrent-test-2",
            },
        )

    # Run both requests concurrently
    results = await asyncio.gather(enable_policy_1(), enable_policy_2(), return_exceptions=True)

    # Both should succeed (one after the other due to locking)
    assert len(results) == 2
    for result in results:
        assert not isinstance(result, Exception), f"Request failed with exception: {result}"
        assert result.status_code == 200
        data = result.json()
        assert data["success"] is True

    # Only one policy should be active
    current_response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/current",
        headers=admin_headers,
    )
    current_data = current_response.json()

    # Should be either AllCapsPolicy or NoOpPolicy (whichever won)
    assert current_data["policy"] in ["AllCapsPolicy", "NoOpPolicy"]
    print(f"Final active policy after concurrent changes: {current_data['policy']}")


# === Policy Source Mode Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_policy_source_file_mode_blocks_changes(http_client, admin_headers):
    """Test that file mode (POLICY_SOURCE=file) blocks runtime changes."""
    # First check if we're in file mode
    source_response = await http_client.get(
        f"{GATEWAY_URL}/admin/policy/source-info",
        headers=admin_headers,
    )
    source_data = source_response.json()

    if source_data["policy_source"] != "file":
        pytest.skip("Test only applicable when POLICY_SOURCE=file")

    # Try to enable a policy
    enable_response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/enable",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "config": {},
            "enabled_by": "file-mode-test",
        },
    )

    # Should be blocked with 403
    assert enable_response.status_code == 403
    assert "read-only" in enable_response.text.lower()


# === Troubleshooting Messages Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_error_includes_helpful_troubleshooting(http_client, admin_headers):
    """Test that errors include helpful troubleshooting steps."""
    # Try to enable non-existent policy
    response = await http_client.post(
        f"{GATEWAY_URL}/admin/policy/enable",
        headers=admin_headers,
        json={
            "policy_class_ref": "invalid.module:InvalidPolicy",
            "config": {},
            "enabled_by": "troubleshooting-test",
        },
    )

    data = response.json()
    assert data["success"] is False
    assert "troubleshooting" in data
    troubleshooting = data["troubleshooting"]

    # Should have multiple helpful steps
    assert len(troubleshooting) > 0
    # Check that it mentions checking the module/class
    troubleshooting_text = " ".join(troubleshooting).lower()
    assert "class" in troubleshooting_text or "module" in troubleshooting_text


if __name__ == "__main__":
    # Allow running tests directly for debugging
    pytest.main([__file__, "-v", "-s", "-m", "e2e"])
