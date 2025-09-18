import pytest

from luthien_proxy.policies.all_caps import AllCapsPolicy
from luthien_proxy.policies.noop import NoOpPolicy


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
async def test_all_caps_streaming_iterator_uppercases():
    policy = AllCapsPolicy()
    out = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={
            "choices": [
                {"delta": {"content": "chunk"}},
                {"delta": {"content": " two"}},
            ]
        },
        request_data={},
    )
    text = "".join(c["delta"]["content"] for c in out.get("choices", []))  # type: ignore[arg-type]
    assert text == "CHUNK TWO"


@pytest.mark.asyncio
async def test_noop_success_hook_returns_response():
    policy = NoOpPolicy()
    resp = {"choices": [{"delta": {"content": "ok"}}]}
    out = await policy.async_post_call_success_hook(resp, None, resp)
    assert out is resp
