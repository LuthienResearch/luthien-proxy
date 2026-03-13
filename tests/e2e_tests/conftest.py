"""Shared fixtures and helpers for E2E tests.

This module provides common infrastructure for E2E tests including:
- Gateway and CLI availability checks
- Policy management context managers
- HTTP client fixtures
"""

import asyncio
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer  # type: ignore[import]

# === Test Configuration ===

# Load .env so test keys match the running gateway.
# Check worktree root first, then fall back to main repo root (worktrees
# share gitignored files like .env with the main checkout).
_repo_root = Path(__file__).resolve().parents[2]
_env_path = _repo_root / ".env"
if not _env_path.exists():
    _main_repo = Path(_repo_root / ".git").resolve()
    if _main_repo.is_file():
        # Worktree: .git is a file pointing to the main repo's .git/worktrees/<name>
        _main_repo = Path(_main_repo.read_text().split("gitdir: ", 1)[1].strip())
        _main_repo = _main_repo.parents[2]  # .git/worktrees/<name> -> repo root
        _env_path = _main_repo / ".env"
load_dotenv(_env_path, override=False)

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))


# === Shared Fixtures ===


@pytest.fixture(scope="session")
def mock_anthropic():
    """Session-scoped mock Anthropic server (runs in background thread).

    Shared across all e2e test files. Use ``mock_anthropic.enqueue(response)``
    before each test to control what the mock returns.

    Requires gateway started with ANTHROPIC_BASE_URL=http://host.docker.internal:18888.
    See docker-compose.mock.yaml.
    """
    server = MockAnthropicServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _reset_mock_server(mock_anthropic: MockAnthropicServer):
    """Drain the mock queue and clear request history before each test.

    Prevents queue contamination across tests caused by leftover enqueued items
    or SDK retries consuming extra queue slots.
    """
    mock_anthropic.drain_queue()
    mock_anthropic.clear_requests()
    yield


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

    # Poll until the policy is actually active (avoids fixed sleep fragility).
    deadline = time.monotonic() + 5.0
    while True:
        current = await get_current_policy(client)
        if policy_class_ref in (current.get("class_ref") or ""):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"Policy {policy_class_ref!r} did not become active within 5 s; current: {current}")
        await asyncio.sleep(0.05)


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
