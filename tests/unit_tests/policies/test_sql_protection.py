import pytest

from luthien_proxy.policies.sql_protection import SQLProtectionPolicy


@pytest.mark.asyncio
async def test_sql_protection_blocks_non_streaming_harmful_call():
    policy = SQLProtectionPolicy()
    data = {"stream": False, "litellm_call_id": "call-1"}
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {
                                "name": "execute_sql",
                                "arguments": '{"query": "DROP TABLE users;"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }

    blocked = await policy.async_post_call_success_hook(data, None, response)
    assert blocked is not response

    choices = blocked.get("choices", [])
    assert choices
    message = choices[0].get("message")
    assert message
    assert message.get("content")
    assert "BLOCKED" in message["content"]
    assert not message.get("tool_calls")


@pytest.mark.asyncio
async def test_sql_protection_blocks_streaming_harmful_call_and_stops():
    policy = SQLProtectionPolicy()
    context = policy.create_stream_context(
        stream_id="stream-1",
        request_data={"litellm_call_id": "call-1"},
    )

    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "tool-1",
                                "type": "function",
                                "function": {
                                    "name": "execute_sql",
                                    "arguments": '{"query": "DROP',
                                },
                            },
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "tool-1",
                                "type": "function",
                                "function": {
                                    "name": "execute_sql",
                                    "arguments": ' TABLE users;"}',
                                },
                            },
                        ]
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"content": "should not reach client"},
                }
            ]
        },
    ]

    consumed = 0

    async def iterator():
        nonlocal consumed
        for chunk in chunks:
            consumed += 1
            yield chunk

    emitted = []
    async for chunk in policy.generate_response_stream(context, iterator()):
        emitted.append(chunk)

    assert consumed == 2  # third chunk must not be pulled
    assert len(emitted) == 1
    choice = emitted[0]["choices"][0]
    assert choice["finish_reason"] == "stop"
    delta = choice.get("delta") or {}
    assert "BLOCKED" in delta.get("content", "")
    assert not delta.get("tool_calls")
    message = choice.get("message") or {}
    assert "BLOCKED" in message.get("content", "")
    assert context.tool_calls == {}
    assert not context.tool_call_active


@pytest.mark.asyncio
async def test_sql_protection_preempt_blocks_harmful_prompt_without_tool_call():
    policy = SQLProtectionPolicy()
    context = policy.create_stream_context(
        stream_id="stream-2",
        request_data={
            "stream": True,
            "litellm_call_id": "call-2",
            "messages": [{"role": "user", "content": "Please drop the orders table immediately."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_sql",
                        "arguments": None,
                    },
                }
            ],
        },
    )

    chunks = [{"choices": [{"finish_reason": "stop", "delta": {}}]}]

    async def iterator():
        for chunk in chunks:
            yield chunk

    emitted = []
    async for chunk in policy.generate_response_stream(context, iterator()):
        emitted.append(chunk)

    assert len(emitted) == 1
    choice = emitted[0]["choices"][0]
    assert choice["finish_reason"] == "stop"
    delta = choice.get("delta") or {}
    assert "BLOCKED" in delta.get("content", "")
    assert not delta.get("tool_calls")
    message = choice.get("message") or {}
    assert "BLOCKED" in message.get("content", "")
