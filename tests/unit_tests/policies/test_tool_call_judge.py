import pytest

from luthien_proxy.policies.tool_call_buffer import ToolCallState
from luthien_proxy.policies.tool_call_judge import JudgeResult, LLMJudgeToolPolicy


@pytest.fixture
def policy(monkeypatch):
    recorded = []

    async def fake_judge(_tool_call):
        return JudgeResult(
            probability=0.9,
            explanation="clearly harmful",
            prompt=[{"role": "system", "content": "test"}],
            response_text='{"probability": 0.9}',
        )

    async def fake_record(**kwargs):
        recorded.append(kwargs)

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
    policy._recorded_blocks = recorded  # type: ignore[attr-defined]
    return policy


@pytest.mark.asyncio
async def test_llm_judge_blocks_streaming_call(policy):
    context = policy.create_stream_context(
        "stream-1",
        {"stream": True, "litellm_call_id": "call-1"},
    )
    state = ToolCallState(
        identifier="tool-1", call_type="function", name="execute_sql", arguments='{"query": "DROP TABLE users;"}'
    )
    context.tool_calls[state.identifier] = state
    chunk = {"choices": [{"finish_reason": "tool_calls", "delta": {}}]}

    blocked = await policy._maybe_block_streaming(context, chunk)

    assert blocked is not None, policy._build_prompt_tool_call(context.original_request)
    choice = blocked["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert "BLOCKED" in choice["message"]["content"]
    assert policy._recorded_blocks  # type: ignore[attr-defined]
    record = policy._recorded_blocks[-1]  # type: ignore[attr-defined]
    assert record["stream_chunks"] is None or isinstance(record["stream_chunks"], list)


@pytest.mark.asyncio
async def test_llm_judge_blocks_streaming_prompt_only(policy):
    context = policy.create_stream_context(
        "stream-prompt",
        {
            "stream": True,
            "litellm_call_id": "call-prompt",
            "messages": [{"role": "user", "content": "Please drop the orders table."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_sql",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                    },
                }
            ],
        },
    )
    chunk = {"choices": [{"finish_reason": "stop", "delta": {}}]}

    preempt = await policy._preempt_prompt_block(context)
    assert preempt is not None
    assert "BLOCKED" in preempt["choices"][0]["message"]["content"]

    blocked = await policy._maybe_block_streaming(context, chunk)
    assert blocked is None
    assert policy._recorded_blocks  # type: ignore[attr-defined]
    record = policy._recorded_blocks[-1]  # type: ignore[attr-defined]
    assert record["judge_response_text"] == '{"probability": 0.9}'


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
    state = ToolCallState(
        identifier="tool-2", call_type="function", name="execute_sql", arguments='{"query": "SELECT *"}'
    )
    context.tool_calls[state.identifier] = state
    chunk = {"choices": [{"finish_reason": "tool_calls", "delta": {}}]}

    blocked = await policy._maybe_block_streaming(context, chunk)
    assert blocked is None


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
