"""Mock e2e tests for audit trail — verify requests are recorded in session history.

Tests that the gateway's session history correctly records requests even when
policies block or modify the response. The audit trail must be complete regardless
of policy outcome.

Cross-cutting concern: audit trail integrity is required for compliance with
ISO 42001 Annex C (risk monitoring) and EU AI Act Article 12 (record-keeping).

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_audit_trail.py -v
"""

import asyncio
import uuid

import httpx
import pytest
from tests.e2e_tests.conftest import ADMIN_API_KEY, API_KEY, GATEWAY_URL, policy_context
from tests.e2e_tests.mock_anthropic.responses import text_response, tool_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_DOGFOOD_SAFETY = "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy"

_HEADERS = {"Authorization": f"Bearer {API_KEY}"}
_ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_API_KEY}"}

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "max_tokens": 100,
}


def _make_session_user_id(session_uuid: str) -> str:
    """Build a metadata.user_id in the format Claude Code uses."""
    return f"user_testaccount__session_{session_uuid}"


async def _send_with_session(
    client: httpx.AsyncClient,
    session_uuid: str,
    content: str = "hello",
) -> httpx.Response:
    """Send a non-streaming request with session tracking metadata."""
    return await client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            **_BASE_REQUEST,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "metadata": {"user_id": _make_session_user_id(session_uuid)},
        },
        headers=_HEADERS,
    )


async def _get_session(client: httpx.AsyncClient, session_uuid: str) -> httpx.Response:
    """Query the history API for a session."""
    return await client.get(
        f"{GATEWAY_URL}/api/history/sessions/{session_uuid}",
        headers=_ADMIN_HEADERS,
    )


async def _poll_for_session(
    client: httpx.AsyncClient,
    session_uuid: str,
    timeout: float = 5.0,
) -> httpx.Response:
    """Poll the session history endpoint until the session appears or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await _get_session(client, session_uuid)
        if resp.status_code == 200:
            return resp
        await asyncio.sleep(0.2)
    pytest.fail(f"Session {session_uuid} not found after {timeout}s")


# =============================================================================
# Section 1: Session stored after normal request
# =============================================================================


@pytest.mark.asyncio
async def test_session_stored_after_passthrough_request(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """A normal (not blocked) request creates a session history entry."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("Hello, how can I help?"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await _send_with_session(client, session_uuid)
        assert resp.status_code == 200

        history_resp = await _poll_for_session(client, session_uuid)

    assert history_resp.status_code == 200, (
        f"Session not found in history (session_uuid={session_uuid}): {history_resp.text}"
    )
    assert session_uuid in history_resp.text, f"Session UUID not present in history response: {history_resp.text}"


# =============================================================================
# Section 2: Session stored after blocked request
# =============================================================================


@pytest.mark.asyncio
async def test_session_stored_after_blocked_request(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """A blocked request (DogfoodSafetyPolicy blocks docker compose down) still creates a session entry."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(tool_response("Bash", {"command": "docker compose down"}))

    async with policy_context(_DOGFOOD_SAFETY, {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _send_with_session(client, session_uuid)

    async with httpx.AsyncClient(timeout=15.0) as client:
        history_resp = await _poll_for_session(client, session_uuid)

    assert history_resp.status_code == 200, (
        f"Blocked request session not found (session_uuid={session_uuid}): {history_resp.text}"
    )
    data = history_resp.json()
    assert data.get("session_id") == session_uuid, (
        f"Session ID not found in response: {data}"
    )


@pytest.mark.asyncio
async def test_session_has_turn_after_blocked_request(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """The session entry has at least one turn recorded even when the request was blocked."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(tool_response("Bash", {"command": "docker compose down"}))

    async with policy_context(_DOGFOOD_SAFETY, {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _send_with_session(client, session_uuid)

    async with httpx.AsyncClient(timeout=15.0) as client:
        history_resp = await _poll_for_session(client, session_uuid)

    assert history_resp.status_code == 200
    data = history_resp.json()
    turns = data.get("turns", [])
    assert len(turns) >= 1, f"Expected at least one turn for blocked request, got {len(turns)}: {data}"


# =============================================================================
# Section 3: Multiple turns — blocked and unblocked
# =============================================================================


@pytest.mark.asyncio
async def test_multiple_turns_blocked_and_unblocked_all_recorded(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """A session with 2 turns (one blocked, one not) has both turns in history."""
    session_uuid = str(uuid.uuid4())

    # Turn 1: blocked (docker compose down triggers DogfoodSafetyPolicy)
    mock_anthropic.enqueue(tool_response("Bash", {"command": "docker compose down"}))
    async with policy_context(_DOGFOOD_SAFETY, {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _send_with_session(client, session_uuid, content="run docker compose down")

    # Turn 2: not blocked (normal text response)
    mock_anthropic.enqueue(text_response("Here is the file listing."))
    async with policy_context(_DOGFOOD_SAFETY, {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _send_with_session(client, session_uuid, content="list the files")

    async with httpx.AsyncClient(timeout=15.0) as client:
        history_resp = await _poll_for_session(client, session_uuid)

    assert history_resp.status_code == 200
    data = history_resp.json()
    turns = data.get("turns", [])
    assert len(turns) >= 2, f"Expected at least 2 turns (blocked + unblocked), got {len(turns)}: {data}"


# =============================================================================
# Section 4: Session isolation
# =============================================================================


@pytest.mark.asyncio
async def test_two_sessions_are_independent(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Two different session UUIDs produce independent history entries."""
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    mock_anthropic.enqueue(text_response("Reply for A"))
    mock_anthropic.enqueue(text_response("Reply for B"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp_a = await _send_with_session(client, session_a, content="message for A")
        assert resp_a.status_code == 200

        resp_b = await _send_with_session(client, session_b, content="message for B")
        assert resp_b.status_code == 200

    async with httpx.AsyncClient(timeout=15.0) as client:
        history_a = await _poll_for_session(client, session_a)
        history_b = await _poll_for_session(client, session_b)

    assert history_a.status_code == 200, f"Session A not found: {history_a.text}"
    assert history_b.status_code == 200, f"Session B not found: {history_b.text}"

    data_a = history_a.json()
    data_b = history_b.json()
    assert data_a.get("session_id") != data_b.get("session_id"), (
        "Two different session UUIDs must produce independent sessions"
    )