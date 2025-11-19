"""Test handling of tool rejection with accompanying user text.

THIS TEST IS PROBABLY WRONG AND NEEDS TO BE FIXED PROPERLY.
"""

from luthien_proxy.llm.llm_format_utils import anthropic_to_openai_request


def test_tool_rejection_with_user_text():
    """Test that tool rejection + user text are both preserved in conversion.

    When a user rejects a tool call and provides new instructions in the same
    message, both the tool rejection and the user text should be converted to
    separate OpenAI messages.
    """
    anthropic_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "write helloworld.py"}],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll create a helloworld.py file for you."},
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "Write",
                        "input": {"file_path": "/tmp/helloworld.py", "content": 'print("Hello, World!")'},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "The user doesn't want to proceed with this tool use.",
                        "is_error": True,
                    },
                    {"type": "text", "text": "[Request interrupted by user for tool use]"},
                    {"type": "text", "text": "Actually make it 'helloborld.py'"},
                ],
            },
        ],
    }

    result = anthropic_to_openai_request(anthropic_request)

    # Should have 4 messages:
    # 1. user: "write helloworld.py"
    # 2. assistant: text + tool_calls
    # 3. tool: rejection result
    # 4. user: "Actually make it 'helloborld.py'" (combined text parts)
    assert len(result["messages"]) == 4

    # Check the tool message
    tool_msg = result["messages"][2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "toolu_123"
    assert "doesn't want to proceed" in tool_msg["content"]

    # Check the user message that follows
    user_msg = result["messages"][3]
    assert user_msg["role"] == "user"
    # Both text parts should be combined
    assert "[Request interrupted by user for tool use]" in user_msg["content"]
    assert "Actually make it 'helloborld.py'" in user_msg["content"]


def test_tool_result_without_text():
    """Test that tool results without accompanying text still work correctly."""
    anthropic_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "Success",
                        "is_error": False,
                    }
                ],
            }
        ],
    }

    result = anthropic_to_openai_request(anthropic_request)

    # Should have 1 tool message
    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "tool"
    assert result["messages"][0]["content"] == "Success"


def test_user_text_without_tool_result():
    """Test that regular user text without tool results still works."""
    anthropic_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
    }

    result = anthropic_to_openai_request(anthropic_request)

    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "user"
    assert result["messages"][0]["content"] == "Hello"
