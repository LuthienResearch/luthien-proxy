import json
import logging

import pytest

from luthien_proxy.policies.conversation_logger import ConversationLoggingPolicy


@pytest.mark.asyncio
async def test_request_logging_includes_messages(caplog):
    policy = ConversationLoggingPolicy()
    caplog.set_level(logging.INFO, logger="luthien.policy.conversation")

    records: list[tuple[str, dict]] = []

    async def writer(debug_type: str, payload: dict):
        records.append((debug_type, payload))

    policy.set_debug_log_writer(writer)

    payload = {
        "litellm_call_id": "call-request",
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ],
    }

    await policy.async_pre_call_hook(data=payload, user_api_key_dict={}, cache={}, call_type="chat")

    record = json.loads(caplog.records[-1].message)
    assert record["direction"] == "request"
    assert record["call_id"] == "call-request"
    assert record["messages"][1]["role"] == "user"
    assert record["messages"][1]["content"] == "hello"
    assert len(records) == 1
    assert records[0][0] == "conversation:turn"


@pytest.mark.asyncio
async def test_non_stream_response_logging_marks_tool_calls(caplog):
    policy = ConversationLoggingPolicy()
    caplog.set_level(logging.INFO, logger="luthien.policy.conversation")

    records: list[tuple[str, dict]] = []

    async def writer(debug_type: str, payload: dict):
        records.append((debug_type, payload))

    policy.set_debug_log_writer(writer)

    data = {
        "litellm_call_id": "call-response",
    }
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {
                                "name": "edit_file",
                                "arguments": '{"path": "foo.txt"}',
                            },
                        }
                    ],
                    "content": None,
                },
            }
        ]
    }

    await policy.async_post_call_success_hook(data=data, user_api_key_dict=None, response=response)

    record = json.loads(caplog.records[-1].message)
    assert record["direction"] == "response"
    assert record["response_type"] == "tool_call"
    assert record["tool_calls"][0]["name"] == "edit_file"
    assert record["tool_calls"][0]["arguments"] == '{"path": "foo.txt"}'
    assert len(records) == 1
    assert records[0][1]["response_type"] == "tool_call"


@pytest.mark.asyncio
async def test_stream_logging_accumulates_tool_call_arguments(caplog):
    policy = ConversationLoggingPolicy()
    caplog.set_level(logging.INFO, logger="luthien.policy.conversation")

    records: list[tuple[str, dict]] = []

    async def writer(debug_type: str, payload: dict):
        records.append((debug_type, payload))

    policy.set_debug_log_writer(writer)

    context = policy.create_stream_context(
        stream_id="stream-1",
        request_data={"litellm_call_id": "stream-1"},
    )

    chunks = [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_a",
                                "type": "function",
                                "function": {
                                    "name": "edit_file",
                                    "arguments": '{"path": "foo"',
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
                                "id": "call_a",
                                "function": {"arguments": ', "op": "append"}'},
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

    async for _ in policy.generate_response_stream(context, iterator()):
        pass

    record = json.loads(caplog.records[-1].message)
    assert record["direction"] == "response"
    assert record["response_type"] == "tool_call"
    assert record["chunks_seen"] == len(chunks)
    assert record["tool_calls"][0]["arguments"] == '{"path": "foo", "op": "append"}'
    assert len(records) == 1
    assert records[0][1]["chunks_seen"] == len(chunks)
