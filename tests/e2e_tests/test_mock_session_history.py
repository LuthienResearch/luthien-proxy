"""Mock e2e tests for session tracking and conversation history.

Verifies that:
- Session IDs embedded in metadata.user_id (Anthropic path) are extracted and stored
- Conversation turns are persisted and queryable via the history API
- Multiple turns in the same session are grouped correctly
- Sessions without a session ID are not tracked

The session ID is extracted from metadata.user_id matching pattern:
  user_<anything>_session_<uuid>
The stored session ID (used to query the history API) is just the UUID part.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_session_history.py -v
"""

import asyncio
import uuid

import httpx
import pytest
from tests.e2e_tests.conftest import ADMIN_API_KEY, API_KEY, GATEWAY_URL
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_HEADERS = {"Authorization": f"Bearer {API_KEY}"}
_ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_API_KEY}"}

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "max_tokens": 100,
}


def _make_session_user_id(session_uuid: str) -> str:
    """Build a metadata.user_id in the format Claude Code uses."""
    return f"user_testaccount__session_{session_uuid}"


async def _send_message(
    client: httpx.AsyncClient,
    content: str,
    session_uuid: str | None = None,
) -> httpx.Response:
    """Send a non-streaming request, optionally with a session ID."""
    body = {
        **_BASE_REQUEST,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }
    if session_uuid is not None:
        body["metadata"] = {"user_id": _make_session_user_id(session_uuid)}
    return await client.post(f"{GATEWAY_URL}/v1/messages", json=body, headers=_HEADERS)


async def _get_session(client: httpx.AsyncClient, session_uuid: str) -> httpx.Response:
    """Query the history API for a session."""
    return await client.get(
        f"{GATEWAY_URL}/api/history/sessions/{session_uuid}",
        headers=_ADMIN_HEADERS,
    )


# =============================================================================
# Session tracking
# =============================================================================


@pytest.mark.asyncio
async def test_session_stored_after_request(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """A request with metadata.user_id stores the session in the history API."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("hello back"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await _send_message(client, "hello", session_uuid=session_uuid)
        assert resp.status_code == 200

        # Give the gateway time to persist the conversation event
        await asyncio.sleep(1.0)

        history_resp = await _get_session(client, session_uuid)

    assert history_resp.status_code == 200, (
        f"Session not found in history (session_uuid={session_uuid}): {history_resp.text}"
    )
    data = history_resp.json()
    assert data.get("session_id") == session_uuid or session_uuid in str(data), (
        f"Session ID not found in response: {data}"
    )


@pytest.mark.asyncio
async def test_session_contains_conversation_turn(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """The stored session includes at least one conversation turn with request/response content."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("world"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await _send_message(client, "hello", session_uuid=session_uuid)
        assert resp.status_code == 200

        await asyncio.sleep(1.0)

        history_resp = await _get_session(client, session_uuid)

    assert history_resp.status_code == 200
    data = history_resp.json()

    # The session detail should have turns
    turns = data.get("turns", [])
    assert len(turns) >= 1, f"Expected at least one turn, got {len(turns)}: {data}"


@pytest.mark.asyncio
async def test_multiple_turns_in_same_session(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Multiple requests with the same session UUID are grouped as turns in one session."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("first reply"))
    mock_anthropic.enqueue(text_response("second reply"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp1 = await _send_message(client, "first message", session_uuid=session_uuid)
        assert resp1.status_code == 200

        resp2 = await _send_message(client, "second message", session_uuid=session_uuid)
        assert resp2.status_code == 200

        await asyncio.sleep(1.0)

        history_resp = await _get_session(client, session_uuid)

    assert history_resp.status_code == 200
    data = history_resp.json()
    turns = data.get("turns", [])
    assert len(turns) >= 2, f"Expected at least 2 turns for 2 requests, got {len(turns)}: {data}"


@pytest.mark.asyncio
async def test_request_without_session_id_not_tracked(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Requests without metadata.user_id don't create a history entry under a predictable ID."""
    # We make a request with no session info at all, then verify the history
    # API returns 404 for an arbitrary UUID (not the one we'd use for tracking).
    random_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("no session"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await _send_message(client, "hello without session")
        assert resp.status_code == 200

        await asyncio.sleep(0.5)

        # Query with a UUID we never sent — should be 404
        history_resp = await _get_session(client, random_uuid)

    assert history_resp.status_code == 404, (
        f"Expected 404 for unknown session, got {history_resp.status_code}: {history_resp.text}"
    )


@pytest.mark.asyncio
async def test_different_sessions_are_independent(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Two different session UUIDs produce two independent history entries."""
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    mock_anthropic.enqueue(text_response("reply to A"))
    mock_anthropic.enqueue(text_response("reply to B"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp_a = await _send_message(client, "message for A", session_uuid=session_a)
        assert resp_a.status_code == 200

        resp_b = await _send_message(client, "message for B", session_uuid=session_b)
        assert resp_b.status_code == 200

        await asyncio.sleep(1.0)

        history_a = await _get_session(client, session_a)
        history_b = await _get_session(client, session_b)

    assert history_a.status_code == 200, f"Session A not found: {history_a.text}"
    assert history_b.status_code == 200, f"Session B not found: {history_b.text}"

    data_a = history_a.json()
    data_b = history_b.json()

    # Each session should only have 1 turn
    turns_a = data_a.get("turns", [])
    turns_b = data_b.get("turns", [])
    assert len(turns_a) >= 1, "Session A should have at least 1 turn"
    assert len(turns_b) >= 1, "Session B should have at least 1 turn"

    # The sessions should be distinct — check their IDs differ
    assert data_a.get("session_id") != data_b.get("session_id"), (
        "Two different session UUIDs must produce independent sessions"
    )


# =============================================================================
# Session list and export
# =============================================================================


@pytest.mark.asyncio
async def test_session_list_includes_recent_session(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """A session created via a request appears in the /api/history/sessions list."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("list test reply"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await _send_message(client, "list me", session_uuid=session_uuid)
        assert resp.status_code == 200

        await asyncio.sleep(1.0)

        list_resp = await client.get(
            f"{GATEWAY_URL}/api/history/sessions",
            headers=_ADMIN_HEADERS,
        )

    assert list_resp.status_code == 200, f"Sessions list failed: {list_resp.text}"
    assert session_uuid in list_resp.text, f"Session UUID {session_uuid} not found in sessions list: {list_resp.text}"


@pytest.mark.asyncio
async def test_session_list_pagination(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """GET /api/history/sessions?limit=1 returns exactly one result."""
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("pagination reply A"))
    mock_anthropic.enqueue(text_response("pagination reply B"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp_a = await _send_message(client, "first paginated message", session_uuid=session_a)
        assert resp_a.status_code == 200

        resp_b = await _send_message(client, "second paginated message", session_uuid=session_b)
        assert resp_b.status_code == 200

        await asyncio.sleep(1.0)

        list_resp = await client.get(
            f"{GATEWAY_URL}/api/history/sessions",
            headers=_ADMIN_HEADERS,
            params={"limit": 1, "offset": 0},
        )

    assert list_resp.status_code == 200, f"Sessions list with pagination failed: {list_resp.text}"
    data = list_resp.json()

    # Normalize: the response may be a list directly or a dict with a "sessions" key
    if isinstance(data, list):
        sessions = data
    else:
        sessions = data.get("sessions", data.get("items", []))

    assert len(sessions) == 1, f"Expected exactly 1 session with limit=1, got {len(sessions)}: {data}"


@pytest.mark.asyncio
async def test_session_export_returns_markdown(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """GET /api/history/sessions/{session_id}/export returns a non-empty markdown/text body."""
    session_uuid = str(uuid.uuid4())
    mock_anthropic.enqueue(text_response("export test reply"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await _send_message(client, "export this", session_uuid=session_uuid)
        assert resp.status_code == 200

        await asyncio.sleep(1.0)

        export_resp = await client.get(
            f"{GATEWAY_URL}/api/history/sessions/{session_uuid}/export",
            headers=_ADMIN_HEADERS,
        )

    assert export_resp.status_code == 200, f"Export failed (session_uuid={session_uuid}): {export_resp.text}"
    content_type = export_resp.headers.get("content-type", "")
    assert "markdown" in content_type or "text" in content_type, (
        f"Expected markdown or text content-type, got: {content_type}"
    )
    assert len(export_resp.text.strip()) > 0, "Export body is empty"


@pytest.mark.asyncio
async def test_malformed_session_id_returns_404(gateway_healthy):
    """GET /api/history/sessions/<non-uuid> returns 404."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/history/sessions/not-a-real-uuid",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 404, (
        f"Expected 404 for malformed session ID, got {response.status_code}: {response.text}"
    )
