import pytest

from luthien_proxy.policies.all_caps import AllCapsPolicy
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.policies.streaming_separator import StreamingSeparatorPolicy


async def _collect_stream(policy, stream_id, request, chunks):
    context = policy.create_stream_context(stream_id, request)

    async def iterator():
        for chunk in chunks:
            yield chunk

    results = []
    async for item in policy.generate_response_stream(context, iterator()):
        results.append(item)
    return results


@pytest.mark.asyncio
async def test_all_caps_success_hook_uppercases():
    policy = AllCapsPolicy()
    out = await policy.async_post_call_success_hook(
        response_obj={
            "choices": [
                {"delta": {"content": "hello"}},
                {"delta": {"content": " world"}},
            ]
        }
    )
    text = "".join(c["delta"]["content"] for c in out.get("choices", []))
    assert text == "HELLO WORLD"


@pytest.mark.asyncio
async def test_all_caps_generate_stream_uppercases_each_chunk():
    policy = AllCapsPolicy()
    chunks = [
        {"choices": [{"delta": {"content": "chunk"}}]},
        {"choices": [{"delta": {"content": " two"}}]},
    ]
    results = await _collect_stream(policy, "caps", {}, chunks)
    merged = "".join(result["choices"][0]["delta"]["content"] for result in results)
    assert merged == "CHUNK TWO"


@pytest.mark.asyncio
async def test_noop_success_hook_returns_response():
    policy = NoOpPolicy()
    resp = {"choices": [{"delta": {"content": "ok"}}]}
    out = await policy.async_post_call_success_hook(resp, None, resp)
    assert out is resp


@pytest.mark.asyncio
async def test_streaming_separator_default_behavior():
    policy = StreamingSeparatorPolicy()
    chunks = [
        {"choices": [{"delta": {"content": "hello"}}]},
        {"choices": [{"delta": {"content": "world"}}]},
    ]
    results = await _collect_stream(policy, "sep-default", {}, chunks)
    assert results[0]["choices"][0]["delta"]["content"] == "hello | "
    assert results[1]["choices"][0]["delta"]["content"] == "world | "


@pytest.mark.asyncio
async def test_streaming_separator_every_n():
    policy = StreamingSeparatorPolicy(options={"every_n": 3, "separator_str": " ||| "})
    chunks = [
        {"choices": [{"delta": {"content": "one"}}]},
        {"choices": [{"delta": {"content": "two"}}]},
        {"choices": [{"delta": {"content": "three"}}]},
    ]
    results = await _collect_stream(policy, "sep-three", {}, chunks)
    assert results[0]["choices"][0]["delta"]["content"] == "one"
    assert results[1]["choices"][0]["delta"]["content"] == "two"
    assert results[2]["choices"][0]["delta"]["content"] == "three ||| "


@pytest.mark.asyncio
async def test_streaming_separator_independent_streams():
    policy = StreamingSeparatorPolicy(options={"every_n": 2, "separator_str": " * "})
    chunks_a = [
        {"choices": [{"delta": {"content": "a1"}}]},
        {"choices": [{"delta": {"content": "a2"}}]},
    ]
    chunks_b = [
        {"choices": [{"delta": {"content": "b1"}}]},
        {"choices": [{"delta": {"content": "b2"}}]},
    ]

    results_a = await _collect_stream(policy, "stream-a", {}, chunks_a)
    results_b = await _collect_stream(policy, "stream-b", {}, chunks_b)

    assert results_a[1]["choices"][0]["delta"]["content"] == "a2 * "
    assert results_b[1]["choices"][0]["delta"]["content"] == "b2 * "


@pytest.mark.asyncio
async def test_streaming_separator_ignores_empty_content():
    policy = StreamingSeparatorPolicy()
    chunks = [
        {"choices": [{"delta": {"content": ""}}]},
        {"choices": [{"delta": {"content": "hello"}}]},
    ]
    results = await _collect_stream(policy, "sep-empty", {}, chunks)
    assert results[0]["choices"][0]["delta"]["content"] == ""
    assert results[1]["choices"][0]["delta"]["content"] == "hello | "


def test_streaming_separator_invalid_every_n():
    with pytest.raises(ValueError, match="every_n must be at least 1"):
        StreamingSeparatorPolicy(options={"every_n": 0})
