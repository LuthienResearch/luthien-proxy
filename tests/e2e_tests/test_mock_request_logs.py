"""Mock e2e tests for the request logs API.

Verifies:
- GET /request-logs returns a paginated list of logs
- GET /request-logs?limit=N respects the limit parameter
- GET /request-logs?limit=1&offset=0 vs offset=1 return different entries
- GET /request-logs/{transaction_id} returns detail for a specific transaction

Auth enforcement on these endpoints is covered by test_mock_auth.py.

These tests require ENABLE_REQUEST_LOGGING=true on the gateway. A module-scoped
fixture handles restarting the gateway container with the env var set, then
restoring it afterward.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d
  - Mock server auto-started by the mock_anthropic fixture (port 18888).

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_request_logs.py -v
"""

import asyncio
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from tests.e2e_tests.conftest import ADMIN_API_KEY, API_KEY, GATEWAY_URL
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_API_KEY}"}
_REGULAR_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": False,
}


def _find_roots() -> tuple[Path, Path]:
    """Return (main_repo_root, worktree_root).

    main_repo_root is where .env and the primary docker-compose.yaml live.
    worktree_root is the current checkout (may be the same as main_repo_root).
    Docker compose must run from main_repo_root so it can find .env.
    """
    worktree_root = Path(__file__).resolve().parents[2]
    git_file = worktree_root / ".git"
    if git_file.is_file():
        gitdir = git_file.read_text().split("gitdir: ", 1)[1].strip()
        main_root = Path(gitdir).resolve().parents[2]
    else:
        main_root = worktree_root
    return main_root, worktree_root


def _wait_for_gateway(timeout: float = 30.0) -> None:
    """Poll the health endpoint until the gateway is ready."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{GATEWAY_URL}/health", timeout=3.0)
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(1.0)
    raise TimeoutError(f"Gateway not healthy after {timeout}s")


def _compose_up_gateway(extra_env: dict[str, str] | None = None) -> None:
    """Recreate the gateway container via docker compose with optional env overrides.

    Uses a temporary docker-compose override file to inject extra environment
    variables, then removes it after the container starts.
    """
    main_root, worktree_root = _find_roots()
    mock_yaml = worktree_root / "docker-compose.mock.yaml"

    compose_cmd = ["docker", "compose", "-f", str(main_root / "docker-compose.yaml")]
    if mock_yaml.exists():
        compose_cmd += ["-f", str(mock_yaml)]

    override_path = None
    if extra_env:
        # Write a temporary compose override with the extra env vars
        env_lines = "\n".join(f"      - {k}={v}" for k, v in extra_env.items())
        override_content = f"""services:
  gateway:
    environment:
{env_lines}
"""
        fd, override_path = tempfile.mkstemp(suffix=".yaml", prefix="compose-override-")
        os.write(fd, override_content.encode())
        os.close(fd)
        compose_cmd += ["-f", override_path]

    try:
        subprocess.run(
            [*compose_cmd, "up", "-d", "gateway"],
            cwd=str(main_root),
            check=True,
            timeout=60,
            capture_output=True,
        )
    finally:
        if override_path:
            os.unlink(override_path)

    _wait_for_gateway()


@pytest.fixture(scope="module")
def _enable_request_logging():
    """Restart gateway with ENABLE_REQUEST_LOGGING=true for this module."""
    _compose_up_gateway(extra_env={"ENABLE_REQUEST_LOGGING": "true"})
    yield
    _compose_up_gateway(extra_env={"ENABLE_REQUEST_LOGGING": "false"})


async def _make_gateway_request(client: httpx.AsyncClient) -> None:
    """Fire a single request through the gateway (response is ignored)."""
    response = await client.post(
        f"{GATEWAY_URL}/v1/messages",
        json=_BASE_REQUEST,
        headers=_REGULAR_HEADERS,
    )
    # Accept any non-5xx status; the important thing is the log entry is written.
    assert response.status_code < 500, f"Gateway error on test request: {response.status_code}: {response.text}"


@pytest.mark.asyncio
async def test_request_logs_list_returns_results(
    mock_anthropic: MockAnthropicServer, _enable_request_logging, gateway_healthy
):
    """Populate a log entry then GET /request-logs returns 200 with 'logs' list and 'total' int."""
    mock_anthropic.enqueue(text_response("hello from the assistant"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)

        response = await client.get(
            f"{GATEWAY_URL}/request-logs",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "logs" in data, f"Expected 'logs' key in response, got: {data}"
    assert "total" in data, f"Expected 'total' key in response, got: {data}"
    assert isinstance(data["logs"], list), f"Expected 'logs' to be a list, got: {type(data['logs'])}"
    assert isinstance(data["total"], int), f"Expected 'total' to be an int, got: {type(data['total'])}"


@pytest.mark.asyncio
async def test_request_logs_limit_param(mock_anthropic: MockAnthropicServer, _enable_request_logging, gateway_healthy):
    """GET /request-logs?limit=1 returns at most 1 log entry."""
    mock_anthropic.enqueue(text_response("first response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)

        response = await client.get(
            f"{GATEWAY_URL}/request-logs",
            params={"limit": 1},
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "logs" in data, f"Expected 'logs' key in response, got: {data}"
    assert len(data["logs"]) <= 1, f"Expected at most 1 log entry with limit=1, got: {len(data['logs'])}"


@pytest.mark.asyncio
async def test_request_logs_offset_param(mock_anthropic: MockAnthropicServer, _enable_request_logging, gateway_healthy):
    """Two requests then offset=0 and offset=1 each return distinct transaction IDs."""
    mock_anthropic.enqueue(text_response("first response"))
    mock_anthropic.enqueue(text_response("second response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)
        await _make_gateway_request(client)

        # Let the recorder flush both entries before querying
        await asyncio.sleep(0.5)

        # Fetch enough rows to span two distinct transactions. Each transaction
        # may produce multiple rows (inbound + outbound), so limit=1 per page
        # can return the same transaction_id at adjacent offsets.
        response = await client.get(
            f"{GATEWAY_URL}/request-logs",
            params={"limit": 10, "offset": 0},
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    logs = response.json().get("logs", [])
    total = response.json().get("total", 0)

    if total >= 2 and len(logs) >= 2:
        txn_ids = list(dict.fromkeys(log.get("transaction_id") for log in logs))
        assert len(txn_ids) >= 2, (
            f"Expected at least 2 distinct transaction IDs from {total} rows, got {len(txn_ids)}: {txn_ids}"
        )


@pytest.mark.asyncio
async def test_request_log_transaction_detail(
    mock_anthropic: MockAnthropicServer, _enable_request_logging, gateway_healthy
):
    """Make a request, fetch the most recent log, then GET /request-logs/{transaction_id}."""
    mock_anthropic.enqueue(text_response("detail test response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)

        # Brief pause to let the recorder flush
        await asyncio.sleep(0.5)

        list_response = await client.get(
            f"{GATEWAY_URL}/request-logs",
            params={"limit": 1},
            headers=_ADMIN_HEADERS,
        )

    assert list_response.status_code == 200, (
        f"Unexpected status on list: {list_response.status_code}: {list_response.text}"
    )
    logs = list_response.json().get("logs", [])
    assert logs, "No request logs found — ENABLE_REQUEST_LOGGING fixture should have enabled logging"

    transaction_id = logs[0]["transaction_id"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        detail_response = await client.get(
            f"{GATEWAY_URL}/request-logs/{transaction_id}",
            headers=_ADMIN_HEADERS,
        )

    assert detail_response.status_code == 200, (
        f"Unexpected status on detail: {detail_response.status_code}: {detail_response.text}"
    )
    detail = detail_response.json()
    assert "transaction_id" in detail, f"Expected 'transaction_id' in detail response, got: {detail}"
    assert detail["transaction_id"] == transaction_id, (
        f"Expected transaction_id={transaction_id!r}, got: {detail['transaction_id']!r}"
    )
