"""Shared fixtures and helpers for E2E tests.

This module provides common infrastructure for E2E tests including:
- Gateway and CLI availability checks
- Policy management context managers
- HTTP client fixtures
"""

import asyncio
import os
import shutil
from contextlib import asynccontextmanager

import httpx
import pytest

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))


# === Shared Fixtures ===


@pytest.fixture
def claude_available():
    """Check if claude CLI is available."""
    if not shutil.which("claude"):
        pytest.skip("Claude CLI not installed - run: npm install -g @anthropic-ai/claude-cli")


@pytest.fixture
def codex_available():
    """Check if codex CLI is available."""
    if not shutil.which("codex"):
        pytest.skip("Codex CLI not installed - see https://developers.openai.com/codex/quickstart/")


@pytest.fixture
async def gateway_healthy():
    """Check if gateway is running and healthy."""
    gateway_base = GATEWAY_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{gateway_base}/health")
            if response.status_code != 200:
                pytest.skip(f"Gateway not healthy: {response.status_code}")
        except httpx.ConnectError:
            pytest.skip(f"Gateway not running at {gateway_base}")


@pytest.fixture
async def http_client():
    """Provide async HTTP client for direct HTTP tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


# === Policy Management Helpers ===


async def set_policy(
    client: httpx.AsyncClient,
    policy_class_ref: str,
    config: dict,
    enabled_by: str = "e2e-test",
) -> None:
    """Set the active policy via admin API.

    Args:
        client: HTTP client to use
        policy_class_ref: Fully qualified policy class reference (e.g., "module:ClassName")
        config: Policy configuration dict
        enabled_by: Identifier for who/what enabled the policy
    """
    gateway_base = GATEWAY_URL.rstrip("/")
    admin_headers = {"Authorization": f"Bearer {ADMIN_API_KEY}"}

    response = await client.post(
        f"{gateway_base}/api/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": policy_class_ref,
            "config": config,
            "enabled_by": enabled_by,
        },
    )
    assert response.status_code == 200, f"Failed to set policy: {response.text}"
    data = response.json()
    assert data.get("success"), f"Policy set failed: {data}"

    # Brief pause to ensure policy is active
    await asyncio.sleep(0.3)


async def get_current_policy(client: httpx.AsyncClient) -> dict:
    """Get current policy information from admin API."""
    gateway_base = GATEWAY_URL.rstrip("/")
    admin_headers = {"Authorization": f"Bearer {ADMIN_API_KEY}"}
    response = await client.get(f"{gateway_base}/api/admin/policy/current", headers=admin_headers)
    assert response.status_code == 200
    return response.json()


@asynccontextmanager
async def policy_context(policy_class_ref: str, config: dict):
    """Context manager that sets up a policy and restores NoOp after test.

    Use this to temporarily activate a policy for a test, ensuring cleanup:

        async with policy_context("luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy", {}):
            # Run test with DebugLoggingPolicy active
            result = await run_claude_code(prompt="...")

    Args:
        policy_class_ref: Fully qualified policy class reference
        config: Policy configuration dict

    Yields:
        None - the policy is active within the context
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Activate the test policy
        await set_policy(client, policy_class_ref, config)
        try:
            yield
        finally:
            # Restore NoOp policy after test
            await set_policy(
                client,
                "luthien_proxy.policies.noop_policy:NoOpPolicy",
                {},
            )
