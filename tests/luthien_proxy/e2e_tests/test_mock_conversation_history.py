"""Mock e2e tests for conversation history storage and export.

These tests verify that the gateway correctly persists conversation **structure**
to the database and exposes it via the history API: message types, tool_call /
tool_result fields, multi-turn ordering, markdown export shape, recency ordering.
Uses the in-process mock Anthropic server — no real API calls.

Scope split (the sibling file has overlapping smoke coverage on purpose):
- ``test_mock_conversation_history.py`` (this file) — *is the parsed structure
  right?* Asserts on ``message_type``, ``tool_name``, ``tool_call_id``,
  ``tool_input``, markdown headers, etc.
- ``test_mock_session_history.py`` — *does session-ID extraction work?*
  Asserts on the ``metadata.user_id`` → session_id mapping path.

Coverage gap: nothing in this file exercises the real Anthropic API end-to-end
(deliberately — these tests verify gateway-internal persistence, not Anthropic
behavior). If the gateway ever drifts in how it parses real Anthropic responses
into ``MessageType.TOOL_CALL``, only the integration tests against the SDK
catch that drift.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_conversation_history.py -v
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response, tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

from luthien_proxy.utils.constants import HISTORY_SESSIONS_MAX_LIMIT

pytestmark = pytest.mark.mock_e2e


_MODEL = "claude-haiku-4-5"
# Persistence is async and has no completion signal exposed to the client, so
# tests poll the history API until the expected turn count lands. 5s is well
# beyond observed real-world latency on a busy CI runner.
_PERSIST_DEADLINE_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.05


# === Helpers ===


async def _post_messages(
    client: httpx.AsyncClient,
    *,
    gateway_url: str,
    api_key: str,
    session_id: str,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict:
    """POST to /v1/messages with an x-session-id header. Asserts 200 and returns body."""
    payload: dict = {
        "model": _MODEL,
        "messages": messages,
        "max_tokens": 100,
    }
    if tools is not None:
        payload["tools"] = tools

    response = await client.post(
        f"{gateway_url}/v1/messages",
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "x-session-id": session_id,
        },
    )
    assert response.status_code == 200, f"Request failed: {response.status_code}: {response.text}"
    return response.json()


async def _get_session_detail(
    client: httpx.AsyncClient,
    *,
    gateway_url: str,
    admin_api_key: str,
    session_id: str,
) -> dict:
    response = await client.get(
        f"{gateway_url}/api/history/sessions/{session_id}",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    assert response.status_code == 200, f"History API failed: {response.status_code}: {response.text}"
    return response.json()


async def _wait_for_session(
    client: httpx.AsyncClient,
    *,
    gateway_url: str,
    admin_api_key: str,
    session_id: str,
    expected_turns: int = 1,
    deadline: float = _PERSIST_DEADLINE_SECONDS,
) -> dict:
    """Poll the history API until the session has at least ``expected_turns`` turns or deadline fires.

    Replaces the older fixed ``asyncio.sleep(1.0)`` pattern: faster on the
    happy path (typical wait <100 ms) and a longer hard budget for the
    unhappy path so slow CI runners don't flake.
    """
    end = time.monotonic() + deadline
    last_status: int | None = None
    last_turn_count: int | None = None
    while time.monotonic() < end:
        response = await client.get(
            f"{gateway_url}/api/history/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_api_key}"},
        )
        last_status = response.status_code
        if response.status_code == 200:
            body = response.json()
            last_turn_count = len(body.get("turns", []))
            if last_turn_count >= expected_turns:
                return body
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"Session {session_id} did not reach {expected_turns} turn(s) within {deadline}s "
        f"(last status={last_status}, last turn count={last_turn_count})"
    )


async def _wait_for_session_in_list(
    client: httpx.AsyncClient,
    *,
    gateway_url: str,
    admin_api_key: str,
    session_id: str,
    deadline: float = _PERSIST_DEADLINE_SECONDS,
) -> list[str]:
    """Poll the session list until ``session_id`` appears, then return the full id list (most-recent first)."""
    end = time.monotonic() + deadline
    last_ids: list[str] = []
    while time.monotonic() < end:
        response = await client.get(
            f"{gateway_url}/api/history/sessions?limit={HISTORY_SESSIONS_MAX_LIMIT}",
            headers={"Authorization": f"Bearer {admin_api_key}"},
        )
        if response.status_code == 200:
            last_ids = [s["session_id"] for s in response.json()["sessions"]]
            if session_id in last_ids:
                return last_ids
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    pytest.fail(f"Session {session_id} did not appear in list within {deadline}s; first 10 ids: {last_ids[:10]}")


async def _export_session_markdown(
    client: httpx.AsyncClient,
    *,
    gateway_url: str,
    admin_api_key: str,
    session_id: str,
) -> str:
    response = await client.get(
        f"{gateway_url}/api/history/sessions/{session_id}/export",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    assert response.status_code == 200, f"Export failed: {response.status_code}: {response.text}"
    return response.text


def _new_session_id(prefix: str) -> str:
    """Generate a session ID with full UUID4 to avoid cross-run collisions."""
    return f"e2e-test-{prefix}-{uuid.uuid4().hex}"


# === Basic conversation storage ===


@pytest.mark.asyncio
async def test_simple_conversation_stored(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """A simple user/assistant exchange is persisted under the supplied session_id."""
    mock_anthropic.enqueue(text_response("Hello, world today."))
    session_id = _new_session_id("simple")

    async with httpx.AsyncClient(timeout=15.0) as client:
        body = await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "Say hello in exactly 3 words."}],
        )
        assert body["type"] == "message"
        # Mock backend is deterministic — text_response() emits a single
        # text block. Assert that shape rather than joining (joining would
        # silently insert separators between blocks if the mock ever emits
        # multiple, masking a regression in stream assembly).
        text_blocks = [b for b in body["content"] if b.get("type") == "text"]
        assert len(text_blocks) == 1, f"Expected single text block from mock, got {len(text_blocks)}: {body['content']}"
        assert text_blocks[0]["text"] == "Hello, world today.", (
            f"Expected exact mock-text round-trip, got: {text_blocks[0]['text']!r}"
        )

        session = await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=1,
        )

    assert session["session_id"] == session_id
    assert len(session["turns"]) == 1
    turn = session["turns"][0]
    assert turn["model"] == _MODEL

    assert len(turn["request_messages"]) == 1
    req = turn["request_messages"][0]
    assert req["message_type"] == "user"
    assert "Say hello in exactly 3 words" in req["content"]

    assert len(turn["response_messages"]) >= 1
    resp = turn["response_messages"][0]
    assert resp["message_type"] == "assistant"
    assert resp["content"], "Expected non-empty response content"


@pytest.mark.asyncio
async def test_multi_turn_conversation_stored(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Two sequential requests sharing a session_id produce two stored turns."""
    mock_anthropic.enqueue(text_response("4"))
    mock_anthropic.enqueue(text_response("12"))
    session_id = _new_session_id("multi")

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "What is 2+2? Just the number."}],
        )
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[
                {"role": "user", "content": "What is 2+2? Just the number."},
                {"role": "assistant", "content": "4"},
                {"role": "user", "content": "Now multiply by 3. Just the number."},
            ],
        )

        session = await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=2,
        )

    assert len(session["turns"]) == 2, f"Expected 2 turns, got {len(session['turns'])}"
    for i, turn in enumerate(session["turns"]):
        assert turn["request_messages"], f"Turn {i + 1} missing request messages"
        assert turn["response_messages"], f"Turn {i + 1} missing response messages"


# === Tool calls ===


_WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the current weather in a location",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name."},
        },
        "required": ["location"],
    },
}

_CALC_TOOL = {
    "name": "calculate",
    "description": "Perform a calculation.",
    "input_schema": {
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
}


@pytest.mark.asyncio
async def test_tool_call_response_stored(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """A tool_use response is persisted as a tool_call message in the response_messages list."""
    expected_tool_id = "toolu_test_response_xyz"
    mock_anthropic.enqueue(tool_response("get_weather", {"location": "Tokyo"}, tool_id=expected_tool_id))
    session_id = _new_session_id("tool")

    async with httpx.AsyncClient(timeout=15.0) as client:
        body = await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
            tools=[_WEATHER_TOOL],
        )
        tool_use_blocks = [b for b in body["content"] if b.get("type") == "tool_use"]
        assert tool_use_blocks, f"Expected tool_use block in response, got: {body['content']}"

        session = await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=1,
        )

    assert len(session["turns"]) == 1
    turn = session["turns"][0]

    assert len(turn["request_messages"]) == 1
    assert "weather" in turn["request_messages"][0]["content"].lower()

    tool_calls = [m for m in turn["response_messages"] if m["message_type"] == "tool_call"]
    assert tool_calls, f"Expected tool_call response message, got: {turn['response_messages']}"
    tc = tool_calls[0]
    assert tc["tool_name"] == "get_weather"
    assert tc["tool_call_id"] == expected_tool_id, (
        f"Expected tool_call_id to round-trip the mock-supplied id, got: {tc['tool_call_id']!r}"
    )
    assert tc["tool_input"] == {"location": "Tokyo"}, (
        f"Expected tool_input to round-trip the original args, got: {tc['tool_input']}"
    )


@pytest.mark.asyncio
async def test_tool_result_in_request_stored(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """A tool_result content block in the request is parsed and stored as a tool_result message."""
    mock_anthropic.enqueue(text_response("It's sunny in Tokyo, 22 degrees."))
    session_id = _new_session_id("toolresult")
    tool_use_id = "toolu_test123"

    messages = [
        {"role": "user", "content": "What's the weather in Tokyo?"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "get_weather",
                    "input": {"location": "Tokyo"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": '{"temperature": 22, "conditions": "sunny"}',
                }
            ],
        },
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=messages,
        )

        session = await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=1,
        )

    assert len(session["turns"]) == 1
    req_messages = session["turns"][0]["request_messages"]

    # The middle assistant message contains a tool_use block; verify the
    # gateway persists that as a tool_call message with the same id, name,
    # and input we sent. A regression that drops in-request tool_use storage
    # would otherwise slip past silently if we only checked the tool_result.
    tool_calls = [m for m in req_messages if m["message_type"] == "tool_call"]
    assert tool_calls, f"Expected assistant tool_use to persist as tool_call message, got: {req_messages}"
    tc = tool_calls[0]
    assert tc["tool_call_id"] == tool_use_id
    assert tc["tool_name"] == "get_weather"
    assert tc["tool_input"] == {"location": "Tokyo"}, (
        f"Expected tool_input to round-trip the original args, got: {tc['tool_input']}"
    )

    tool_results = [m for m in req_messages if m["message_type"] == "tool_result"]
    assert tool_results, f"Expected tool_result message, got: {req_messages}"
    tr = tool_results[0]
    assert tr["tool_call_id"] == tool_use_id
    # Mock backend is deterministic, so the persisted content should round-trip
    # the exact JSON we sent — no hedging needed.
    assert tr["content"] == '{"temperature": 22, "conditions": "sunny"}', (
        f"Expected exact tool_result content round-trip, got: {tr['content']!r}"
    )


# === Markdown export ===


@pytest.mark.asyncio
async def test_markdown_export_basic(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Markdown export contains session header, turn marker, and user/assistant content."""
    mock_anthropic.enqueue(text_response("Paris is the capital of France."))
    session_id = _new_session_id("export")

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "What is the capital of France?"}],
        )
        await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=1,
        )
        markdown = await _export_session_markdown(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
        )

    assert f"# Conversation History: {session_id}" in markdown
    assert "## Turn 1" in markdown
    assert "### User" in markdown
    assert "capital of France" in markdown
    assert "### Assistant" in markdown


@pytest.mark.asyncio
async def test_markdown_export_multi_turn(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Markdown export handles multi-turn conversations and shows both turns."""
    mock_anthropic.enqueue(text_response("10"))
    mock_anthropic.enqueue(text_response("20"))
    session_id = _new_session_id("export-multi")

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "What is 5+5?"}],
        )
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[
                {"role": "user", "content": "What is 5+5?"},
                {"role": "assistant", "content": "10"},
                {"role": "user", "content": "What is that times 2?"},
            ],
        )
        await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=2,
        )
        markdown = await _export_session_markdown(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
        )

    assert "## Turn 1" in markdown
    assert "## Turn 2" in markdown
    assert "5+5" in markdown
    assert "times 2" in markdown


@pytest.mark.asyncio
async def test_markdown_export_with_tool_calls(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Markdown export surfaces tool calls when the assistant invoked one."""
    mock_anthropic.enqueue(tool_response("calculate", {"expression": "123*456"}))
    session_id = _new_session_id("export-tools")
    user_prompt = "Calculate 123 * 456 using the calculate tool"

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[_CALC_TOOL],
        )
        await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=1,
        )
        markdown = await _export_session_markdown(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
        )

    assert f"# Conversation History: {session_id}" in markdown
    assert "## Turn 1" in markdown
    assert "### User" in markdown
    # User prompt should be rendered under the User header — symmetric with the
    # other markdown tests (e.g. test_markdown_export_basic asserts on user content).
    assert user_prompt in markdown, f"Expected user prompt {user_prompt!r} in export. Got:\n{markdown}"
    # service._format_message_markdown emits literal `### Tool Call` for
    # MessageType.TOOL_CALL, with `**Tool:** `<name>`` on the next line.
    # Assert on the formatted Tool: line, not the bare tool name — the user
    # prompt also contains "calculate", so a substring search would be
    # vacuously true even if the tool_use block weren't rendered at all.
    assert "### Tool Call" in markdown, f"Expected `### Tool Call` header in export. Got:\n{markdown}"
    assert "**Tool:** `calculate`" in markdown, (
        f"Expected `**Tool:** `calculate`` line under `### Tool Call`. Got:\n{markdown}"
    )


# === Session list ===


@pytest.mark.asyncio
async def test_session_appears_in_list(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """A new session shows up in the session-list endpoint."""
    mock_anthropic.enqueue(text_response("hi"))
    session_id = _new_session_id("list")

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "Hello"}],
        )
        # _wait_for_session_in_list polls the (max-limit) listing until our
        # session appears or the deadline fires.
        await _wait_for_session_in_list(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
        )


@pytest.mark.asyncio
async def test_session_list_ordered_by_recency(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """The session list orders most-recent first."""
    mock_anthropic.enqueue(text_response("first"))
    mock_anthropic.enqueue(text_response("second"))
    session_id_1 = _new_session_id("order1")
    session_id_2 = _new_session_id("order2")

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id_1,
            messages=[{"role": "user", "content": "First session"}],
        )
        # 0.5s ensures the two sessions land in different DB timestamp buckets
        # (Postgres timestamps are microsecond-precision; SQLite is millisecond).
        # Anything sub-millisecond risks identical timestamps and ambiguous order.
        await asyncio.sleep(0.5)
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id_2,
            messages=[{"role": "user", "content": "Second session"}],
        )
        # Wait for session_id_2 to appear; session_id_1 was created earlier
        # and will be in the list once session_id_2 is.
        session_ids = await _wait_for_session_in_list(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id_2,
        )

    assert session_id_1 in session_ids and session_id_2 in session_ids, (
        f"Both sessions must be present in list before checking order. "
        f"Looking for {session_id_1} and {session_id_2}; got first 10: {session_ids[:10]}"
    )
    assert session_ids.index(session_id_2) < session_ids.index(session_id_1), (
        f"Expected {session_id_2} to appear before {session_id_1}, got order: {session_ids[:10]}"
    )
