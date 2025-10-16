import pytest

from luthien_proxy.policies.tool_call_judge import JudgeResult, LLMJudgeToolPolicy


@pytest.fixture
def policy(monkeypatch):
    recorded = []
    emitted_events = []

    async def fake_judge(_tool_call):
        return JudgeResult(
            probability=0.9,
            explanation="clearly harmful",
            prompt=[{"role": "system", "content": "test"}],
            response_text='{"probability": 0.9}',
        )

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_emit_policy_event(*, call_id, event_type, metadata):
        emitted_events.append(
            {
                "call_id": call_id,
                "event_type": event_type,
                "metadata": metadata,
            }
        )

    policy = LLMJudgeToolPolicy(
        options={
            "model": "judge-model",
            "api_base": "http://judge",
            "api_key": "key",
            "probability_threshold": 0.5,
        }
    )
    monkeypatch.setattr(policy, "_call_judge", fake_judge)
    monkeypatch.setattr(policy, "_record_judge_block", fake_record)
    monkeypatch.setattr(policy, "_emit_policy_event", fake_emit_policy_event)
    policy._recorded_blocks = recorded  # type: ignore[attr-defined]
    policy._emitted_events = emitted_events  # type: ignore[attr-defined]
    return policy


@pytest.mark.asyncio
async def test_llm_judge_blocks_streaming_call(policy):
    context = policy.create_stream_context(
        "stream-1",
        {"stream": True, "litellm_call_id": "call-1"},
    )

    # Simulate streaming chunks that contain a harmful tool call
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "tool-1",
                                "type": "function",
                                "function": {
                                    "name": "execute_sql",
                                    "arguments": '{"query": "DROP TABLE users;"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]

    async def stream_generator():
        for chunk in chunks:
            yield chunk

    results = []
    async for output_chunk in policy.generate_response_stream(context, stream_generator()):
        results.append(output_chunk)

    # Should get role chunk first, then BLOCKED response instead of the tool call
    assert len(results) == 2
    # First chunk is the role
    assert results[0]["choices"][0]["delta"]["role"] == "assistant"
    # Second chunk is the BLOCKED response
    blocked = results[1]
    choice = blocked["choices"][0]
    assert choice["finish_reason"] == "stop"
    # Check for BLOCKED in either message or delta
    content = choice.get("message", {}).get("content") or choice.get("delta", {}).get("content")
    assert "BLOCKED" in content
    assert policy._recorded_blocks  # type: ignore[attr-defined]
    record = policy._recorded_blocks[-1]  # type: ignore[attr-defined]
    assert record["stream_chunks"] is None or isinstance(record["stream_chunks"], list)
    events = policy._emitted_events  # type: ignore[attr-defined]
    assert len(events) == 2
    assert events[0]["event_type"] == "judge_request_sent"
    request_metadata = events[0]["metadata"]
    assert request_metadata["tool_call"]["id"] == "tool-1"
    assert request_metadata["judge_parameters"]["model"] == "judge-model"
    assert request_metadata["judge_parameters"]["probability_threshold"] == 0.5
    assert events[1]["event_type"] == "judge_response_received"
    response_metadata = events[1]["metadata"]
    assert response_metadata["tool_call"]["id"] == "tool-1"
    assert response_metadata["judge_response"]["probability"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_llm_judge_allows_low_probability(monkeypatch):
    async def fake_judge(_tool_call):
        return JudgeResult(
            probability=0.1,
            explanation="benign",
            prompt=[{"role": "system", "content": "test"}],
            response_text='{"probability": 0.1}',
        )

    policy = LLMJudgeToolPolicy(
        options={
            "model": "judge",
            "api_base": "http://judge",
            "api_key": "key",
            "probability_threshold": 0.5,
        }
    )
    monkeypatch.setattr(policy, "_call_judge", fake_judge)
    context = policy.create_stream_context("stream-2", {"stream": True, "litellm_call_id": "call-2"})

    # Simulate streaming chunks with a benign tool call
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "tool-2",
                                "type": "function",
                                "function": {
                                    "name": "execute_sql",
                                    "arguments": '{"query": "SELECT *"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]

    async def stream_generator():
        for chunk in chunks:
            yield chunk

    results = []
    async for output_chunk in policy.generate_response_stream(context, stream_generator()):
        results.append(output_chunk)

    # Should get role chunk first, then the tool call since probability is low
    assert len(results) == 2
    # First chunk is the role
    assert results[0]["choices"][0]["delta"]["role"] == "assistant"
    # Second chunk is the allowed tool call
    result = results[1]
    choice = result["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    # Check it has tool calls and is not blocked
    delta = choice.get("delta", {})
    assert delta.get("tool_calls") is not None, "Expected tool_calls in delta"
    # Content should be None or empty for tool calls, not "BLOCKED"
    content = choice.get("message", {}).get("content") or choice.get("delta", {}).get("content")
    if content:
        assert "BLOCKED" not in content


@pytest.mark.asyncio
async def test_llm_judge_blocks_non_streaming(policy):
    response = {
        "id": "resp-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tool-3",
                            "type": "function",
                            "function": {
                                "name": "execute_sql",
                                "arguments": '{"query": "DROP TABLE accounts;"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    blocked = await policy.async_post_call_success_hook(
        {"stream": False, "litellm_call_id": "call-3"},
        None,
        response,
    )

    assert blocked["id"] == "resp-1"
    message = blocked["choices"][0]["message"]["content"]
    assert "BLOCKED" in message
    assert policy._recorded_blocks  # type: ignore[attr-defined]
