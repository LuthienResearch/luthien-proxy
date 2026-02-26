"""E2E tests for conversation history storage and export.

These tests verify that conversations are correctly stored in the database
and can be retrieved via the history API, including:
- Simple text messages (user/assistant)
- Tool calls and tool results
- Markdown export functionality

Prerequisites:
- Gateway must be running (docker compose up gateway)
- Valid API credentials in env or .env
"""

import asyncio

import httpx
import pytest
from tests.e2e_tests.conftest import (
    ADMIN_API_KEY,
    API_KEY,
    GATEWAY_URL,
)

# === Helper Functions ===


async def make_chat_request(
    client: httpx.AsyncClient,
    messages: list[dict],
    session_id: str,
    model: str = "gpt-4o-mini",
    tools: list[dict] | None = None,
) -> dict:
    """Make a chat completion request through the gateway.

    Args:
        client: HTTP client
        messages: List of message dicts
        session_id: Session identifier for grouping
        model: Model to use
        tools: Optional tools to include

    Returns:
        Response data dict
    """
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 100,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    response = await client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-session-id": session_id,
        },
    )
    assert response.status_code == 200, f"Request failed: {response.text}"
    return response.json()


async def get_session_detail(client: httpx.AsyncClient, session_id: str) -> dict:
    """Fetch session detail from history API.

    Args:
        client: HTTP client
        session_id: Session to fetch

    Returns:
        Session detail dict
    """
    response = await client.get(
        f"{GATEWAY_URL}/api/history/sessions/{session_id}",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )
    assert response.status_code == 200, f"History API failed: {response.text}"
    return response.json()


async def export_session_markdown(client: httpx.AsyncClient, session_id: str) -> str:
    """Export session as markdown.

    Args:
        client: HTTP client
        session_id: Session to export

    Returns:
        Markdown string
    """
    response = await client.get(
        f"{GATEWAY_URL}/api/history/sessions/{session_id}/export",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )
    assert response.status_code == 200, f"Export failed: {response.text}"
    return response.text


# === Basic Conversation Storage Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_simple_conversation_stored(http_client, gateway_healthy):
    """Verify a simple user/assistant exchange is stored correctly.

    This test:
    1. Sends a simple message with a unique session ID
    2. Waits for persistence
    3. Retrieves the session via history API
    4. Verifies request and response messages are present
    """
    import uuid

    session_id = f"e2e-test-simple-{uuid.uuid4().hex[:8]}"

    # Make a simple request
    response_data = await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "Say hello in exactly 3 words."}],
        session_id=session_id,
    )

    # Verify we got a response
    assert "choices" in response_data
    assert len(response_data["choices"]) > 0
    assistant_content = response_data["choices"][0]["message"]["content"]
    assert assistant_content, "Expected non-empty assistant response"

    # Wait for async persistence
    await asyncio.sleep(1.0)

    # Fetch from history API
    session_detail = await get_session_detail(http_client, session_id)

    # Verify session structure
    assert session_detail["session_id"] == session_id
    assert len(session_detail["turns"]) == 1

    turn = session_detail["turns"][0]
    assert turn["model"] == "gpt-4o-mini"

    # Verify request message
    assert len(turn["request_messages"]) == 1
    req_msg = turn["request_messages"][0]
    assert req_msg["message_type"] == "user"
    assert "Say hello in exactly 3 words" in req_msg["content"]

    # Verify response message
    assert len(turn["response_messages"]) >= 1
    resp_msg = turn["response_messages"][0]
    assert resp_msg["message_type"] == "assistant"
    assert resp_msg["content"], "Expected non-empty response content"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_multi_turn_conversation_stored(http_client, gateway_healthy):
    """Verify multi-turn conversations are stored with all turns.

    This test:
    1. Makes two sequential requests with the same session ID
    2. Verifies both turns are stored under the same session
    """
    import uuid

    session_id = f"e2e-test-multi-{uuid.uuid4().hex[:8]}"

    # First turn
    await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "What is 2+2? Just the number."}],
        session_id=session_id,
    )

    # Second turn (with conversation history)
    await make_chat_request(
        http_client,
        messages=[
            {"role": "user", "content": "What is 2+2? Just the number."},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "Now multiply by 3. Just the number."},
        ],
        session_id=session_id,
    )

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Fetch session
    session_detail = await get_session_detail(http_client, session_id)

    # Should have 2 turns
    assert len(session_detail["turns"]) == 2, f"Expected 2 turns, got {len(session_detail['turns'])}"

    # Both turns should have request and response messages
    for i, turn in enumerate(session_detail["turns"]):
        assert len(turn["request_messages"]) > 0, f"Turn {i + 1} missing request messages"
        assert len(turn["response_messages"]) > 0, f"Turn {i + 1} missing response messages"


# === Tool Call Storage Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_tool_call_response_stored(http_client, gateway_healthy):
    """Verify tool call responses are correctly stored.

    This test:
    1. Makes a request that should trigger a tool call
    2. Verifies the tool call is stored in the response messages
    """
    import uuid

    session_id = f"e2e-test-tool-{uuid.uuid4().hex[:8]}"

    # Define a simple tool
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather in a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state, e.g. San Francisco, CA",
                        },
                    },
                    "required": ["location"],
                },
            },
        }
    ]

    # Make a request that should trigger a tool call
    response_data = await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "What's the weather like in Tokyo?"}],
        session_id=session_id,
        tools=tools,
    )

    # Check if we got a tool call in the response
    choice = response_data["choices"][0]
    has_tool_call = choice.get("message", {}).get("tool_calls") is not None

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Fetch from history
    session_detail = await get_session_detail(http_client, session_id)

    assert len(session_detail["turns"]) == 1
    turn = session_detail["turns"][0]

    # Verify request
    assert len(turn["request_messages"]) == 1
    assert "weather" in turn["request_messages"][0]["content"].lower()

    # Verify response - should have either content or tool call
    assert len(turn["response_messages"]) >= 1

    if has_tool_call:
        # If model made a tool call, we should see a tool_call message type
        tool_call_msgs = [m for m in turn["response_messages"] if m["message_type"] == "tool_call"]
        assert len(tool_call_msgs) > 0, f"Expected tool_call message. Response messages: {turn['response_messages']}"

        # Verify tool call has expected fields
        tc_msg = tool_call_msgs[0]
        assert tc_msg["tool_name"] == "get_weather", f"Expected get_weather, got {tc_msg['tool_name']}"
        assert tc_msg["tool_call_id"] is not None


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_tool_result_in_request_stored(http_client, gateway_healthy):
    """Verify tool results in requests are correctly stored.

    This test simulates a conversation with tool result messages.
    """
    import uuid

    session_id = f"e2e-test-toolresult-{uuid.uuid4().hex[:8]}"

    # Simulate a conversation with a tool result
    messages = [
        {"role": "user", "content": "What's the weather in Tokyo?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_test123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"location": "Tokyo"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_test123",
            "content": '{"temperature": 22, "conditions": "sunny"}',
        },
    ]

    await make_chat_request(
        http_client,
        messages=messages,
        session_id=session_id,
    )

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Fetch from history
    session_detail = await get_session_detail(http_client, session_id)

    assert len(session_detail["turns"]) == 1
    turn = session_detail["turns"][0]

    # Request should have user message and tool result
    # (The assistant message with tool_calls might be filtered or parsed differently)
    req_messages = turn["request_messages"]
    assert len(req_messages) >= 2, f"Expected at least 2 request messages, got {len(req_messages)}"

    # Find the tool result message
    tool_results = [m for m in req_messages if m["message_type"] == "tool_result"]
    assert len(tool_results) > 0, f"Expected tool_result message. Request messages: {req_messages}"

    tr_msg = tool_results[0]
    assert tr_msg["tool_call_id"] == "call_test123"
    assert "temperature" in tr_msg["content"] or "22" in tr_msg["content"]


# === Markdown Export Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_markdown_export_basic(http_client, gateway_healthy):
    """Verify basic markdown export contains expected content.

    This test:
    1. Creates a conversation
    2. Exports as markdown
    3. Verifies structure and content
    """
    import uuid

    session_id = f"e2e-test-export-{uuid.uuid4().hex[:8]}"

    # Create a simple conversation
    await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "What is the capital of France?"}],
        session_id=session_id,
    )

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Export as markdown
    markdown = await export_session_markdown(http_client, session_id)

    # Verify markdown structure
    assert f"# Conversation History: {session_id}" in markdown
    assert "## Turn 1" in markdown
    assert "### User" in markdown
    assert "capital of France" in markdown
    assert "### Assistant" in markdown


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_markdown_export_multi_turn(http_client, gateway_healthy):
    """Verify markdown export handles multi-turn conversations."""
    import uuid

    session_id = f"e2e-test-export-multi-{uuid.uuid4().hex[:8]}"

    # First turn
    await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "What is 5+5?"}],
        session_id=session_id,
    )

    # Second turn
    await make_chat_request(
        http_client,
        messages=[
            {"role": "user", "content": "What is 5+5?"},
            {"role": "assistant", "content": "10"},
            {"role": "user", "content": "What is that times 2?"},
        ],
        session_id=session_id,
    )

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Export
    markdown = await export_session_markdown(http_client, session_id)

    # Should have both turns
    assert "## Turn 1" in markdown
    assert "## Turn 2" in markdown
    assert "5+5" in markdown
    assert "times 2" in markdown


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_markdown_export_with_tool_calls(http_client, gateway_healthy):
    """Verify markdown export includes tool call information."""
    import uuid

    session_id = f"e2e-test-export-tools-{uuid.uuid4().hex[:8]}"

    tools = [
        {
            "type": "function",
            "function": {
                "name": "calculate",
                "description": "Perform a calculation",
                "parameters": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
        }
    ]

    # Make request with tools
    response_data = await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "Calculate 123 * 456 using the calculate tool"}],
        session_id=session_id,
        tools=tools,
    )

    # Check if model made a tool call
    choice = response_data["choices"][0]
    has_tool_call = choice.get("message", {}).get("tool_calls") is not None

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Export
    markdown = await export_session_markdown(http_client, session_id)

    # Basic structure should be present
    assert f"# Conversation History: {session_id}" in markdown
    assert "## Turn 1" in markdown
    assert "### User" in markdown

    # If there was a tool call, it should be in the export
    if has_tool_call:
        assert "### Tool Call" in markdown or "calculate" in markdown.lower()


# === Session List Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_session_appears_in_list(http_client, gateway_healthy):
    """Verify new sessions appear in the session list."""
    import uuid

    session_id = f"e2e-test-list-{uuid.uuid4().hex[:8]}"

    # Create a conversation
    await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "Hello"}],
        session_id=session_id,
    )

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Get session list
    response = await http_client.get(
        f"{GATEWAY_URL}/api/history/sessions",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )
    assert response.status_code == 200

    data = response.json()
    session_ids = [s["session_id"] for s in data["sessions"]]

    assert session_id in session_ids, f"Session {session_id} not in list. Sessions: {session_ids[:5]}..."


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_session_list_ordered_by_recency(http_client, gateway_healthy):
    """Verify session list is ordered by most recent activity."""
    import uuid

    # Create two sessions with a delay between them
    session_id_1 = f"e2e-test-order1-{uuid.uuid4().hex[:8]}"
    session_id_2 = f"e2e-test-order2-{uuid.uuid4().hex[:8]}"

    await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "First session"}],
        session_id=session_id_1,
    )

    await asyncio.sleep(0.5)

    await make_chat_request(
        http_client,
        messages=[{"role": "user", "content": "Second session"}],
        session_id=session_id_2,
    )

    # Wait for persistence
    await asyncio.sleep(1.0)

    # Get session list
    response = await http_client.get(
        f"{GATEWAY_URL}/api/history/sessions",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )
    assert response.status_code == 200

    data = response.json()
    session_ids = [s["session_id"] for s in data["sessions"]]

    # Session 2 should appear before session 1 (more recent first)
    if session_id_1 in session_ids and session_id_2 in session_ids:
        idx_1 = session_ids.index(session_id_1)
        idx_2 = session_ids.index(session_id_2)
        assert idx_2 < idx_1, f"Expected {session_id_2} before {session_id_1}. Order: {session_ids[:5]}..."
