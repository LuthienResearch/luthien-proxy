commit f353cfd031db3dd09e54e186a90ded63803796bf
Author: Jai <jai@jai.one>
Date:   Fri Feb 13 17:03:38 2026 -0800

    docs: web UI consolidation strategy with endpoint inventory
    
    Comprehensive analysis of all 28 web UI and API endpoints. Documents
    navigation gaps, code duplication, and overlapping views. Proposes
    phased consolidation with shared nav bar as the highest-impact quick win.
    
    Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>

diff --git a/tests/e2e_tests/test_conversation_live_view.py b/tests/e2e_tests/test_conversation_live_view.py
new file mode 100644
index 0000000..73e2f3c
--- /dev/null
+++ b/tests/e2e_tests/test_conversation_live_view.py
@@ -0,0 +1,270 @@
+"""E2E tests for the conversation live view feature.
+
+These tests verify that the live view page:
+- Is accessible at /conversation/live/{session_id}
+- Shows all messages in the conversation
+- Shows tool calls and their results
+- Shows policy divergences (original vs modified)
+
+Prerequisites:
+- Gateway must be running (docker compose up gateway)
+- Valid API credentials in env or .env
+"""
+
+import asyncio
+import uuid
+
+import httpx
+import pytest
+from tests.e2e_tests.conftest import (
+    ADMIN_API_KEY,
+    API_KEY,
+    GATEWAY_URL,
+    policy_context,
+)
+
+
+async def make_chat_request(
+    client: httpx.AsyncClient,
+    messages: list[dict],
+    session_id: str,
+    model: str = "gpt-4o-mini",
+    tools: list[dict] | None = None,
+) -> dict:
+    """Make a chat completion request through the gateway."""
+    payload = {
+        "model": model,
+        "messages": messages,
+        "max_tokens": 100,
+        "stream": False,
+    }
+    if tools:
+        payload["tools"] = tools
+
+    response = await client.post(
+        f"{GATEWAY_URL}/v1/chat/completions",
+        json=payload,
+        headers={
+            "Authorization": f"Bearer {API_KEY}",
+            "x-session-id": session_id,
+        },
+    )
+    assert response.status_code == 200, f"Request failed: {response.text}"
+    return response.json()
+
+
+async def get_live_view_page(client: httpx.AsyncClient, session_id: str) -> httpx.Response:
+    """Fetch the live view HTML page."""
+    return await client.get(
+        f"{GATEWAY_URL}/conversation/live/{session_id}",
+        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
+    )
+
+
+async def get_session_detail(client: httpx.AsyncClient, session_id: str) -> dict:
+    """Fetch session detail from history API (same data the live view uses)."""
+    response = await client.get(
+        f"{GATEWAY_URL}/history/api/sessions/{session_id}",
+        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
+    )
+    assert response.status_code == 200, f"History API failed: {response.text}"
+    return response.json()
+
+
+@pytest.mark.e2e
+@pytest.mark.asyncio
+async def test_live_view_page_accessible(http_client, gateway_healthy):
+    """Verify the live view page returns HTML for any session ID."""
+    response = await get_live_view_page(http_client, "test-session-123")
+
+    assert response.status_code == 200
+    assert "text/html" in response.headers.get("content-type", "")
+    assert "Live Conversation View" in response.text
+    assert "conversation_live" in response.text or "live-badge" in response.text
+
+
+@pytest.mark.e2e
+@pytest.mark.asyncio
+async def test_live_view_shows_messages(http_client, gateway_healthy):
+    """Verify the live view displays messages from a conversation.
+
+    This test:
+    1. Sends a message through the proxy with a unique session ID
+    2. Waits for persistence
+    3. Verifies the session data API returns the messages
+    4. Verifies the live view page is accessible for this session
+    """
+    session_id = f"e2e-live-view-{uuid.uuid4().hex[:8]}"
+
+    response_data = await make_chat_request(
+        http_client,
+        messages=[{"role": "user", "content": "Say the word 'pineapple' and nothing else."}],
+        session_id=session_id,
+    )
+
+    assert "choices" in response_data
+    assert len(response_data["choices"]) > 0
+
+    # Wait for async persistence
+    await asyncio.sleep(1.5)
+
+    # Verify session data is available (the API the live view polls)
+    session_detail = await get_session_detail(http_client, session_id)
+    assert session_detail["session_id"] == session_id
+    assert len(session_detail["turns"]) == 1
+
+    turn = session_detail["turns"][0]
+    assert len(turn["request_messages"]) >= 1
+    assert len(turn["response_messages"]) >= 1
+
+    # Verify request message content
+    user_msgs = [m for m in turn["request_messages"] if m["message_type"] == "user"]
+    assert len(user_msgs) >= 1
+    assert "pineapple" in user_msgs[0]["content"]
+
+    # Verify response message content
+    assistant_msgs = [m for m in turn["response_messages"] if m["message_type"] == "assistant"]
+    assert len(assistant_msgs) >= 1
+    assert assistant_msgs[0]["content"], "Expected non-empty assistant response"
+
+    # Verify the live view page loads for this session
+    page_response = await get_live_view_page(http_client, session_id)
+    assert page_response.status_code == 200
+
+
+@pytest.mark.e2e
+@pytest.mark.asyncio
+async def test_live_view_shows_tool_calls(http_client, gateway_healthy):
+    """Verify the live view data includes tool call information."""
+    session_id = f"e2e-live-tools-{uuid.uuid4().hex[:8]}"
+
+    tools = [
+        {
+            "type": "function",
+            "function": {
+                "name": "get_temperature",
+                "description": "Get temperature for a city",
+                "parameters": {
+                    "type": "object",
+                    "properties": {
+                        "city": {"type": "string", "description": "City name"},
+                    },
+                    "required": ["city"],
+                },
+            },
+        }
+    ]
+
+    response_data = await make_chat_request(
+        http_client,
+        messages=[{"role": "user", "content": "What's the temperature in Paris?"}],
+        session_id=session_id,
+        tools=tools,
+    )
+
+    choice = response_data["choices"][0]
+    has_tool_call = choice.get("message", {}).get("tool_calls") is not None
+
+    await asyncio.sleep(1.5)
+
+    session_detail = await get_session_detail(http_client, session_id)
+    assert len(session_detail["turns"]) == 1
+
+    turn = session_detail["turns"][0]
+
+    if has_tool_call:
+        tool_call_msgs = [m for m in turn["response_messages"] if m["message_type"] == "tool_call"]
+        assert len(tool_call_msgs) > 0, f"Expected tool_call in response. Messages: {turn['response_messages']}"
+        assert tool_call_msgs[0]["tool_name"] == "get_temperature"
+
+
+@pytest.mark.e2e
+@pytest.mark.asyncio
+async def test_live_view_shows_policy_divergence(http_client, gateway_healthy):
+    """Verify the live view data shows divergences when a policy modifies content.
+
+    Uses the AllCapsPolicy to force a visible modification, then checks
+    that the session detail includes original vs final request data.
+    """
+    session_id = f"e2e-live-diff-{uuid.uuid4().hex[:8]}"
+
+    async with policy_context(
+        "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
+        {},
+    ):
+        await make_chat_request(
+            http_client,
+            messages=[{"role": "user", "content": "hello world"}],
+            session_id=session_id,
+        )
+
+    await asyncio.sleep(1.5)
+
+    session_detail = await get_session_detail(http_client, session_id)
+    assert len(session_detail["turns"]) == 1
+
+    turn = session_detail["turns"][0]
+
+    # AllCapsPolicy should modify the request (uppercasing content)
+    assert turn["request_was_modified"], (
+        f"Expected AllCapsPolicy to modify the request. Turn data: request_was_modified={turn['request_was_modified']}"
+    )
+    assert turn["original_request_messages"] is not None, "Expected original_request_messages to be present"
+
+    # The original should have lowercase, the final should have uppercase
+    final_user_msgs = [m for m in turn["request_messages"] if m["message_type"] == "user"]
+    original_user_msgs = [m for m in turn["original_request_messages"] if m["message_type"] == "user"]
+
+    assert len(final_user_msgs) >= 1
+    assert len(original_user_msgs) >= 1
+
+    # Original should contain the lowercase version
+    assert "hello world" in original_user_msgs[0]["content"].lower()
+    # Final should be uppercased by AllCapsPolicy
+    assert "HELLO WORLD" in final_user_msgs[0]["content"]
+
+
+@pytest.mark.e2e
+@pytest.mark.asyncio
+async def test_live_view_updates_with_new_turns(http_client, gateway_healthy):
+    """Verify the live view data updates when new turns arrive.
+
+    Simulates the polling behavior by making two requests and checking
+    that both turns appear in the session detail.
+    """
+    session_id = f"e2e-live-update-{uuid.uuid4().hex[:8]}"
+
+    # First turn
+    await make_chat_request(
+        http_client,
+        messages=[{"role": "user", "content": "What is 2+2?"}],
+        session_id=session_id,
+    )
+
+    await asyncio.sleep(1.0)
+
+    # First poll - should see 1 turn
+    detail_1 = await get_session_detail(http_client, session_id)
+    assert len(detail_1["turns"]) == 1
+
+    # Second turn
+    await make_chat_request(
+        http_client,
+        messages=[
+            {"role": "user", "content": "What is 2+2?"},
+            {"role": "assistant", "content": "4"},
+            {"role": "user", "content": "Now multiply by 3."},
+        ],
+        session_id=session_id,
+    )
+
+    await asyncio.sleep(1.0)
+
+    # Second poll - should see 2 turns
+    detail_2 = await get_session_detail(http_client, session_id)
+    assert len(detail_2["turns"]) == 2, f"Expected 2 turns, got {len(detail_2['turns'])}"
+
+    # Both turns should have messages
+    for i, turn in enumerate(detail_2["turns"]):
+        assert len(turn["request_messages"]) > 0, f"Turn {i + 1} missing request messages"
+        assert len(turn["response_messages"]) > 0, f"Turn {i + 1} missing response messages"
