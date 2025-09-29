import pytest

from luthien_proxy.policies.all_caps import AllCapsPolicy
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.policies.streaming_separator import StreamingSeparatorPolicy


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


@pytest.mark.asyncio
async def test_streaming_separator_default_config():
    """Test StreamingSeparatorPolicy with default config (every 1 token, ' | ' separator)."""
    policy = StreamingSeparatorPolicy()

    # First token should get separator
    out1 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "hello"}}]},
        request_data={},
    )
    assert out1["choices"][0]["delta"]["content"] == "hello | "

    # Second token should also get separator
    out2 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "world"}}]},
        request_data={},
    )
    assert out2["choices"][0]["delta"]["content"] == "world | "


@pytest.mark.asyncio
async def test_streaming_separator_every_n():
    """Test StreamingSeparatorPolicy with every_n=3."""
    policy = StreamingSeparatorPolicy(options={"every_n": 3, "separator_str": " ||| "})

    # Tokens 1-2 should not get separator
    out1 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "one"}}]},
        request_data={},
    )
    assert out1["choices"][0]["delta"]["content"] == "one"

    out2 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "two"}}]},
        request_data={},
    )
    assert out2["choices"][0]["delta"]["content"] == "two"

    # Token 3 should get separator
    out3 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "three"}}]},
        request_data={},
    )
    assert out3["choices"][0]["delta"]["content"] == "three ||| "

    # Tokens 4-5 should not get separator
    out4 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "four"}}]},
        request_data={},
    )
    assert out4["choices"][0]["delta"]["content"] == "four"

    out5 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "five"}}]},
        request_data={},
    )
    assert out5["choices"][0]["delta"]["content"] == "five"

    # Token 6 should get separator
    out6 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "six"}}]},
        request_data={},
    )
    assert out6["choices"][0]["delta"]["content"] == "six ||| "


@pytest.mark.asyncio
async def test_streaming_separator_custom_separator():
    """Test StreamingSeparatorPolicy with custom separator string."""
    policy = StreamingSeparatorPolicy(options={"every_n": 2, "separator_str": " -> "})

    out1 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "first"}}]},
        request_data={},
    )
    assert out1["choices"][0]["delta"]["content"] == "first"

    out2 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "second"}}]},
        request_data={},
    )
    assert out2["choices"][0]["delta"]["content"] == "second -> "


@pytest.mark.asyncio
async def test_streaming_separator_empty_content():
    """Test StreamingSeparatorPolicy handles empty content gracefully."""
    policy = StreamingSeparatorPolicy()

    # Empty content should not increment counter
    out1 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": ""}}]},
        request_data={},
    )
    assert out1["choices"][0]["delta"]["content"] == ""

    # Next non-empty content should be token 1
    out2 = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {"content": "hello"}}]},
        request_data={},
    )
    assert out2["choices"][0]["delta"]["content"] == "hello | "


@pytest.mark.asyncio
async def test_streaming_separator_missing_content():
    """Test StreamingSeparatorPolicy handles missing content field gracefully."""
    policy = StreamingSeparatorPolicy()

    # Missing content field should not cause errors
    out = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={"choices": [{"delta": {}}]},
        request_data={},
    )
    assert "content" not in out["choices"][0]["delta"]


@pytest.mark.asyncio
async def test_streaming_separator_multiple_choices():
    """Test StreamingSeparatorPolicy handles multiple choices."""
    policy = StreamingSeparatorPolicy()

    out = await policy.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None,
        response={
            "choices": [
                {"delta": {"content": "first"}},
                {"delta": {"content": "second"}},
            ]
        },
        request_data={},
    )
    # Both choices should get separators (each increments the counter)
    assert out["choices"][0]["delta"]["content"] == "first | "
    assert out["choices"][1]["delta"]["content"] == "second | "


def test_streaming_separator_invalid_every_n():
    """Test StreamingSeparatorPolicy rejects invalid every_n values."""
    with pytest.raises(ValueError, match="every_n must be at least 1"):
        StreamingSeparatorPolicy(options={"every_n": 0})
