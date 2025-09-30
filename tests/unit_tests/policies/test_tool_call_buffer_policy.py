import json

import pytest

from luthien_proxy.policies.tool_call_buffer import ToolCallBufferPolicy


@pytest.mark.asyncio
async def test_streaming_tool_call_gets_buffered_and_logged():
    policy = ToolCallBufferPolicy()

    records: list[tuple[str, dict]] = []

    async def writer(debug_type: str, payload: dict):
        records.append((debug_type, payload))

    policy.set_debug_log_writer(writer)

    context = policy.create_stream_context(
        stream_id="stream-1",
        request_data={"litellm_call_id": "call-1", "litellm_trace_id": "trace-1"},
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
                                    "name": "shell",
                                    "arguments": '{"command": ["echo"',
                                },
                            }
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
                                    "arguments": ' ,"hello"]}',
                                },
                            }
                        ]
                    },
                }
            ]
        },
    ]

    async def iterator():
        for chunk in chunks:
            yield chunk

    seen = []
    async for emitted in policy.generate_response_stream(context, iterator()):
        seen.append(emitted)

    assert seen == chunks

    tool_logs = [payload for dtype, payload in records if dtype == "conversation:tool-call"]
    assert len(tool_logs) == 1
    tool_log = tool_logs[0]
    assert tool_log["call_id"] == "call-1"
    assert tool_log["trace_id"] == "trace-1"
    assert tool_log["chunks_buffered"] == len(chunks)
    assert tool_log["tool_calls"][0]["name"] == "shell"
    args = json.loads(tool_log["tool_calls"][0]["arguments"])
    assert args["command"] == ["echo", "hello"]


@pytest.mark.asyncio
async def test_non_tool_chunks_passthrough():
    policy = ToolCallBufferPolicy()

    records: list[tuple[str, dict]] = []

    async def writer(debug_type: str, payload: dict):
        records.append((debug_type, payload))

    policy.set_debug_log_writer(writer)

    context = policy.create_stream_context(
        stream_id="stream-2",
        request_data={"litellm_call_id": "call-2"},
    )

    chunk = {"choices": [{"delta": {"content": "Hi there"}}]}

    async def iterator():
        yield chunk

    seen = []
    async for emitted in policy.generate_response_stream(context, iterator()):
        seen.append(emitted)

    assert seen == [chunk]
    assert all(dtype != "conversation:tool-call" for dtype, _ in records)


@pytest.mark.asyncio
async def test_non_stream_tool_call_logged():
    policy = ToolCallBufferPolicy()

    records: list[tuple[str, dict]] = []

    async def writer(debug_type: str, payload: dict):
        records.append((debug_type, payload))

    policy.set_debug_log_writer(writer)

    data = {"litellm_call_id": "call-3", "stream": False}
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {
                                "name": "search_docs",
                                "arguments": json.dumps({"query": "hello"}),
                            },
                        }
                    ]
                },
            }
        ]
    }

    result = await policy.async_post_call_success_hook(data=data, user_api_key_dict=None, response=response)
    assert result == response

    tool_logs = [payload for dtype, payload in records if dtype == "conversation:tool-call"]
    assert len(tool_logs) == 1
    assert tool_logs[0]["tool_calls"][0]["name"] == "search_docs"
    assert tool_logs[0]["tool_calls"][0]["arguments"] == json.dumps({"query": "hello"})


@pytest.mark.asyncio
async def test_tool_call_flush_without_finish_reason():
    policy = ToolCallBufferPolicy()

    records: list[tuple[str, dict]] = []

    async def writer(debug_type: str, payload: dict):
        records.append((debug_type, payload))

    policy.set_debug_log_writer(writer)

    context = policy.create_stream_context(
        stream_id="stream-3",
        request_data={"litellm_call_id": "call-4"},
    )

    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "tool-2",
                                "type": "function",
                                "function": {
                                    "name": "shell",
                                    "arguments": '{"command": ["touch',
                                },
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "tool-2",
                                "type": "function",
                                "function": {
                                    "name": "shell",
                                    "arguments": json.dumps({"command": ["touch", "file.txt"]}),
                                },
                            }
                        ]
                    }
                }
            ]
        },
    ]

    async def iterator():
        for chunk in chunks:
            yield chunk

    seen = []
    async for emitted in policy.generate_response_stream(context, iterator()):
        seen.append(emitted)

    assert seen == chunks
    tool_logs = [payload for dtype, payload in records if dtype == "conversation:tool-call"]
    assert len(tool_logs) == 1
    assert tool_logs[0]["tool_calls"][0]["arguments"] == json.dumps({"command": ["touch", "file.txt"]})
