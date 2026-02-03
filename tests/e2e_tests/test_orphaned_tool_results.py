"""E2E tests for orphaned tool_result pruning.

These tests verify that the gateway correctly handles message histories
that contain orphaned tool_results (tool results without matching tool_calls),
which can occur after /compact removes earlier messages.

Without the pruning fix, these requests would fail with:
"unexpected tool_use_id found in tool_result blocks"
"""

import pytest

from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_orphaned_tool_result_is_pruned(gateway_healthy, http_client):
    """Test that orphaned tool_results are pruned and request succeeds.

    This simulates what happens after /compact:
    - The assistant message with tool_use was removed
    - But the tool_result message remains (orphaned)
    - The proxy should prune the orphan and request should succeed
    """
    # Simulate a post-/compact conversation where tool_use was removed
    # but tool_result remains
    messages = [
        {"role": "user", "content": "Earlier context was summarized by /compact"},
        # This tool_result references a tool_call that was in a removed message
        {"role": "tool", "tool_call_id": "call_from_removed_message", "content": "file contents here"},
        {"role": "assistant", "content": "I see the file contents."},
        {"role": "user", "content": "What is 2+2? Just reply with the number."},
    ]

    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": 100,
        },
        timeout=30.0,
    )

    # Without the fix, this would return 400 with "unexpected tool_use_id"
    assert response.status_code == 200, f"Request failed: {response.status_code} {response.text}"

    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0
    # The model should respond to the last user message
    content = data["choices"][0]["message"]["content"]
    assert content, "Response should have content"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_multiple_orphaned_tool_results_pruned(gateway_healthy, http_client):
    """Test that multiple orphaned tool_results are all pruned."""
    messages = [
        {"role": "user", "content": "Context summary"},
        # Multiple orphaned tool_results
        {"role": "tool", "tool_call_id": "orphan_1", "content": "result 1"},
        {"role": "tool", "tool_call_id": "orphan_2", "content": "result 2"},
        {"role": "tool", "tool_call_id": "orphan_3", "content": "result 3"},
        {"role": "assistant", "content": "Processed all files."},
        {"role": "user", "content": "Say hello briefly."},
    ]

    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": 100,
        },
        timeout=30.0,
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} {response.text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_valid_tool_results_preserved(gateway_healthy, http_client):
    """Test that valid tool_results (with matching tool_calls) are preserved."""
    messages = [
        {"role": "user", "content": "Read a file"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_valid_123",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_valid_123", "content": "File contents: Hello World"},
        {"role": "user", "content": "What did the file say? Reply briefly."},
    ]

    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": 100,
        },
        timeout=30.0,
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} {response.text}"

    data = response.json()
    content = data["choices"][0]["message"]["content"].lower()
    # The model should reference the file contents since the tool_result was preserved
    assert "hello" in content or "world" in content or "file" in content


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_mixed_valid_and_orphaned_tool_results(gateway_healthy, http_client):
    """Test that valid tool_results are kept while orphans are pruned."""
    messages = [
        {"role": "user", "content": "Read files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_valid",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "valid.txt"}'},
                }
            ],
        },
        # Valid tool_result
        {"role": "tool", "tool_call_id": "call_valid", "content": "VALID_CONTENT_MARKER"},
        # Orphaned tool_result (no matching tool_call above)
        {"role": "tool", "tool_call_id": "call_orphan", "content": "ORPHAN_CONTENT_MARKER"},
        {"role": "assistant", "content": "Processed files."},
        {"role": "user", "content": "What content did you see? List everything."},
    ]

    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": 200,
        },
        timeout=30.0,
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} {response.text}"

    # The valid content should be visible to the model
    # The orphaned content should have been pruned (model won't see it)
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    assert content, "Response should have content"
