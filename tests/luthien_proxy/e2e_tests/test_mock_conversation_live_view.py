"""Mock e2e tests for the conversation live-view page.

Scope: the ``/conversation/live/{id}`` UI route + the underlying history-API
data the page polls. Distinct from ``test_mock_conversation_history.py``,
which exercises the persistence/parsing pipeline (message types, markdown
export, session listing).

What this file asserts:
- The live-view HTML page is reachable and serves the expected static shell.
- Single-turn conversations show up in the history-API response that the
  live-view client polls.
- Tool-use round-trips into a ``tool_call`` message on that response.
- Policy-driven response modification surfaces as
  ``response_was_modified`` + diverging ``original_response_messages``
  (the diff the live-view UI renders).
- Multi-turn updates land as additional turns on subsequent polls.

Uses the in-process mock Anthropic server — no real API calls, deterministic
responses. Uses the ``_wait_for_session`` polling helper from conftest
instead of fixed sleeps.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_conversation_live_view.py -v
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import _wait_for_session, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response, tool_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e


_MODEL = "claude-haiku-4-5"


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


async def _get_live_view_page(
    client: httpx.AsyncClient,
    *,
    gateway_url: str,
    admin_api_key: str,
    session_id: str,
) -> httpx.Response:
    """Fetch the live-view HTML page for a given session_id."""
    return await client.get(
        f"{gateway_url}/conversation/live/{session_id}",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )


def _new_session_id(prefix: str) -> str:
    """Generate a session ID with full UUID4 to avoid cross-run collisions."""
    return f"e2e-test-live-view-{prefix}-{uuid.uuid4().hex}"


# === Tests ===


@pytest.mark.asyncio
async def test_live_view_page_accessible(
    gateway_healthy,
    gateway_url,
    admin_api_key,
):
    """The live-view route serves the static shell for any session_id.

    The page itself fetches data client-side from the history API; the route
    handler does no DB lookup, so a never-seen session_id still returns 200.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _get_live_view_page(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id="never-existed-session-id",
        )

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    # The static template's <title> and the live indicator's CSS class.
    # If the template ever moves these, both assertions catch it together.
    assert "Live Conversation View" in response.text
    assert "live-badge" in response.text


@pytest.mark.asyncio
async def test_live_view_shows_messages(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """A single-turn conversation appears in the history-API data the page polls.

    Native Anthropic response shape: ``body["content"][n]["text"]`` — not
    OpenAI ``choices[0].message.content``. The original e2e test asserted
    on the OpenAI shape and broke when the gateway switched to native.
    """
    mock_anthropic.enqueue(text_response("pineapple"))
    session_id = _new_session_id("messages")

    async with httpx.AsyncClient(timeout=15.0) as client:
        body = await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "Say the word 'pineapple' and nothing else."}],
        )
        assert body["type"] == "message"
        text_blocks = [b for b in body["content"] if b.get("type") == "text"]
        assert len(text_blocks) == 1, f"Expected single text block from mock, got: {body['content']}"
        assert text_blocks[0]["text"] == "pineapple"

        session = await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=1,
        )

        page = await _get_live_view_page(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
        )

    assert session["session_id"] == session_id
    assert len(session["turns"]) == 1
    turn = session["turns"][0]

    user_msgs = [m for m in turn["request_messages"] if m["message_type"] == "user"]
    assert user_msgs, f"Expected a user request message, got: {turn['request_messages']}"
    assert "pineapple" in user_msgs[0]["content"]

    assistant_msgs = [m for m in turn["response_messages"] if m["message_type"] == "assistant"]
    assert assistant_msgs, f"Expected an assistant response message, got: {turn['response_messages']}"
    assert assistant_msgs[0]["content"] == "pineapple", (
        f"Expected exact mock-text round-trip, got: {assistant_msgs[0]['content']!r}"
    )

    assert page.status_code == 200


@pytest.mark.asyncio
async def test_live_view_shows_tool_calls(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """A tool_use response surfaces as a ``tool_call`` message in the live-view data.

    Tool definitions use native Anthropic shape ``{name, description, input_schema}``
    — not the OpenAI ``{type: "function", function: {...}}`` wrapper.
    """
    expected_tool_id = "toolu_test_live_view_xyz"
    mock_anthropic.enqueue(tool_response("get_temperature", {"city": "Paris"}, tool_id=expected_tool_id))
    session_id = _new_session_id("tool")

    weather_tool = {
        "name": "get_temperature",
        "description": "Get temperature for a city",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        body = await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "What's the temperature in Paris?"}],
            tools=[weather_tool],
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

    tool_calls = [m for m in turn["response_messages"] if m["message_type"] == "tool_call"]
    assert tool_calls, f"Expected tool_call in response_messages, got: {turn['response_messages']}"
    tc = tool_calls[0]
    assert tc["tool_name"] == "get_temperature"
    assert tc["tool_call_id"] == expected_tool_id, (
        f"Expected tool_call_id to round-trip the mock-supplied id, got: {tc['tool_call_id']!r}"
    )
    assert tc["tool_input"] == {"city": "Paris"}, (
        f"Expected tool_input to round-trip the original args, got: {tc['tool_input']}"
    )


@pytest.mark.asyncio
async def test_live_view_shows_policy_divergence(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """When a policy modifies the response, the live-view data carries both versions.

    The live-view UI renders a diff between ``original_response_messages``
    (pre-policy) and ``response_messages`` (post-policy). AllCapsPolicy is
    a deterministic text-modifier (uppercases assistant text), so the
    divergence is forced and predictable — no real LLM needed.
    """
    mock_anthropic.enqueue(text_response("hello world"))
    session_id = _new_session_id("divergence")

    async with policy_context(
        "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
        {},
        gateway_url=gateway_url,
        admin_api_key=admin_api_key,
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _post_messages(
                client,
                gateway_url=gateway_url,
                api_key=api_key,
                session_id=session_id,
                messages=[{"role": "user", "content": "reply with the exact lowercase text: hello world"}],
            )
            session = await _wait_for_session(
                client,
                gateway_url=gateway_url,
                admin_api_key=admin_api_key,
                session_id=session_id,
                expected_turns=1,
            )

    assert len(session["turns"]) == 1
    turn = session["turns"][0]

    assert turn["response_was_modified"], f"Expected AllCapsPolicy to flag the response as modified. Turn: {turn}"
    assert turn["original_response_messages"] is not None, (
        "Expected original_response_messages on a policy-modified turn"
    )

    final_assistant = [m for m in turn["response_messages"] if m["message_type"] == "assistant"]
    original_assistant = [m for m in turn["original_response_messages"] if m["message_type"] == "assistant"]
    assert final_assistant, f"Expected a final assistant message, got: {turn['response_messages']}"
    assert original_assistant, f"Expected an original assistant message, got: {turn['original_response_messages']}"

    # Mock backend returns "hello world" unchanged; AllCapsPolicy uppercases it.
    assert original_assistant[0]["content"] == "hello world", (
        f"Expected pre-policy content to round-trip the mock text, got: {original_assistant[0]['content']!r}"
    )
    assert final_assistant[0]["content"] == "HELLO WORLD", (
        f"Expected post-policy content to be uppercased, got: {final_assistant[0]['content']!r}"
    )


@pytest.mark.asyncio
async def test_live_view_updates_with_new_turns(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Subsequent requests on the same session_id show up as new turns.

    Simulates the live-view UI's polling behavior: the page polls the same
    history-API endpoint and re-renders when the turn count grows.
    """
    mock_anthropic.enqueue(text_response("4"))
    mock_anthropic.enqueue(text_response("12"))
    session_id = _new_session_id("update")

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[{"role": "user", "content": "What is 2+2?"}],
        )
        first = await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=1,
        )
        assert len(first["turns"]) == 1

        await _post_messages(
            client,
            gateway_url=gateway_url,
            api_key=api_key,
            session_id=session_id,
            messages=[
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "4"},
                {"role": "user", "content": "Now multiply by 3."},
            ],
        )
        second = await _wait_for_session(
            client,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
            session_id=session_id,
            expected_turns=2,
        )

    assert len(second["turns"]) == 2, f"Expected 2 turns, got {len(second['turns'])}"
    for i, turn in enumerate(second["turns"]):
        assert turn["request_messages"], f"Turn {i + 1} missing request messages"
        assert turn["response_messages"], f"Turn {i + 1} missing response messages"
