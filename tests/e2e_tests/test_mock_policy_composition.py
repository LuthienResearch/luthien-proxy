"""Mock e2e tests for MultiSerialPolicy composition.

MultiSerialPolicy uses a "backend chain" model: policy N+1 is policy N's backend.
This means **response** processing order is the REVERSE of config order:

  Config:   [PolicyA, PolicyB]
  Request:  PolicyA → PolicyB → LLM
  Response: LLM → PolicyB → PolicyA

So when designing composition tests, the replacement target must match the text
as it exists *at that stage in the response pipeline*, not at the final output.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_policy_composition.py -v
"""

import json

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context
from tests.e2e_tests.mock_anthropic.responses import stream_response, text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}

_ALL_CAPS = "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
_STRING_REPLACE = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
_MULTI_SERIAL = "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy"


def _multi_config(*policies: tuple[str, dict]) -> dict:
    """Build MultiSerialPolicy config from (class_ref, config) tuples."""
    return {"policies": [{"class": cls, "config": cfg} for cls, cfg in policies]}


# =============================================================================
# Order matters: AllCaps first in config → runs LAST on response
# =============================================================================


@pytest.mark.asyncio
async def test_composition_allcaps_then_replace_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Config [AllCaps, Replace] — response order is reversed: Replace runs first, AllCaps last.

    Response pipeline: LLM("hello world") → Replace("hello"→"hi") → AllCaps → "HI WORLD"

    Replace target must match the raw lowercase LLM output because Replace sees it first.
    AllCaps then uppercases whatever Replace produced.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _multi_config(
        (_ALL_CAPS, {}),
        (_STRING_REPLACE, {"replacements": [["hello", "hi"]], "match_capitalization": False}),
    )
    async with policy_context(_MULTI_SERIAL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    # Replace saw "hello world" first → "hi world"
    # AllCaps saw "hi world" last → "HI WORLD"
    assert text == "HI WORLD", f"Unexpected output: {text!r}"


# =============================================================================
# Order matters: Replace first in config → runs LAST on response
# =============================================================================


@pytest.mark.asyncio
async def test_composition_replace_then_allcaps_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Config [Replace, AllCaps] — response order is reversed: AllCaps runs first, Replace last.

    Response pipeline: LLM("hello world") → AllCaps → Replace("HELLO"→"GOODBYE") → "GOODBYE WORLD"

    Replace target must be uppercase ("HELLO") because AllCaps runs before Replace on the response.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _multi_config(
        (_STRING_REPLACE, {"replacements": [["HELLO", "GOODBYE"]], "match_capitalization": False}),
        (_ALL_CAPS, {}),
    )
    async with policy_context(_MULTI_SERIAL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    # AllCaps saw "hello world" first → "HELLO WORLD"
    # Replace saw "HELLO WORLD" last → "GOODBYE WORLD"
    assert text == "GOODBYE WORLD", f"Unexpected output: {text!r}"


# =============================================================================
# Three-policy chain
# =============================================================================


@pytest.mark.asyncio
async def test_composition_three_policy_chain_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Three-policy chain verifies all three stages apply in sequence.

    Config: [Replace1("HELLO"→"HI"), AllCaps, Replace2("world"→"universe")]
    Response pipeline (reversed):
      LLM("hello world")
      → Replace2("world"→"universe") → "hello universe"
      → AllCaps → "HELLO UNIVERSE"
      → Replace1("HELLO"→"HI") → "HI UNIVERSE"
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _multi_config(
        (_STRING_REPLACE, {"replacements": [["HELLO", "HI"]], "match_capitalization": False}),
        (_ALL_CAPS, {}),
        (_STRING_REPLACE, {"replacements": [["world", "universe"]], "match_capitalization": False}),
    )
    async with policy_context(_MULTI_SERIAL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    assert text == "HI UNIVERSE", f"Unexpected output: {text!r}"


# =============================================================================
# Composition in streaming mode
# =============================================================================


@pytest.mark.asyncio
async def test_composition_allcaps_then_replace_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Config [AllCaps, Replace] streaming: Replace processes each chunk first, AllCaps last.

    Response pipeline per chunk: chunk → Replace("hello"→"hi") → AllCaps
    - "hello " → "hi " → "HI "
    - "world" → "world" → "WORLD"
    Result: "HI WORLD"
    """
    mock_anthropic.enqueue(stream_response("hello world", chunks=["hello ", "world"]))

    config = _multi_config(
        (_ALL_CAPS, {}),
        (_STRING_REPLACE, {"replacements": [["hello", "hi"]], "match_capitalization": False}),
    )
    async with policy_context(_MULTI_SERIAL, config):
        collected = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "POST",
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=_HEADERS,
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            event = json.loads(line[len("data:") :].strip())
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                collected.append(delta["text"])

    full_text = "".join(collected)
    assert full_text == "HI WORLD", f"Unexpected streaming output: {full_text!r}"
