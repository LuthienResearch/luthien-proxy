"""Mock e2e tests for MultiSerialPolicy composition.

MultiSerialPolicy runs all policies in **list order** for both request and response:

  Config:   [PolicyA, PolicyB]
  Request:  PolicyA → PolicyB → LLM
  Response: LLM → PolicyA → PolicyB

So when designing composition tests, the replacement target must match the text
as it exists *at that stage in the response pipeline* (after earlier policies
in the list have already transformed it).

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_policy_composition.py -v
"""

import json

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import stream_response, text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}

_ALL_CAPS = "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
_STRING_REPLACE = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
_MULTI_SERIAL = "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy"


def _multi_config(*policies: tuple[str, dict]) -> dict:
    """Build MultiSerialPolicy config from (class_ref, config) tuples."""
    return {"policies": [{"class": cls, "config": cfg} for cls, cfg in policies]}


# =============================================================================
# Order matters: AllCaps first in config → runs FIRST on response
# =============================================================================


@pytest.mark.asyncio
async def test_composition_allcaps_then_replace_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Config [AllCaps, Replace] — both run in list order on response.

    Response pipeline: LLM("hello world") → AllCaps → "HELLO WORLD" → Replace("HELLO"→"HI") → "HI WORLD"

    AllCaps uppercases first, so Replace target must match the uppercased text.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _multi_config(
        (_ALL_CAPS, {}),
        (_STRING_REPLACE, {"replacements": [["HELLO", "HI"]], "match_capitalization": False}),
    )
    async with policy_context(_MULTI_SERIAL, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    # AllCaps saw "hello world" first → "HELLO WORLD"
    # Replace saw "HELLO WORLD" next → "HI WORLD"
    assert text == "HI WORLD", f"Unexpected output: {text!r}"


# =============================================================================
# Order matters: Replace first in config → runs FIRST on response
# =============================================================================


@pytest.mark.asyncio
async def test_composition_replace_then_allcaps_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Config [Replace, AllCaps] — both run in list order on response.

    Response pipeline: LLM("hello world") → Replace("hello"→"goodbye") → "goodbye world" → AllCaps → "GOODBYE WORLD"

    Replace sees the raw lowercase LLM output first, then AllCaps uppercases everything.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _multi_config(
        (_STRING_REPLACE, {"replacements": [["hello", "goodbye"]], "match_capitalization": False}),
        (_ALL_CAPS, {}),
    )
    async with policy_context(_MULTI_SERIAL, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    # Replace saw "hello world" first → "goodbye world"
    # AllCaps saw "goodbye world" next → "GOODBYE WORLD"
    assert text == "GOODBYE WORLD", f"Unexpected output: {text!r}"


# =============================================================================
# Three-policy chain
# =============================================================================


@pytest.mark.asyncio
async def test_composition_three_policy_chain_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Three-policy chain verifies all three stages apply in list order.

    Config: [Replace1("hello"→"hi"), AllCaps, Replace2("WORLD"→"UNIVERSE")]
    Response pipeline (list order):
      LLM("hello world")
      → Replace1("hello"→"hi") → "hi world"
      → AllCaps → "HI WORLD"
      → Replace2("WORLD"→"UNIVERSE") → "HI UNIVERSE"
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _multi_config(
        (_STRING_REPLACE, {"replacements": [["hello", "hi"]], "match_capitalization": False}),
        (_ALL_CAPS, {}),
        (_STRING_REPLACE, {"replacements": [["WORLD", "UNIVERSE"]], "match_capitalization": False}),
    )
    async with policy_context(_MULTI_SERIAL, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
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
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Config [AllCaps, Replace] streaming: AllCaps processes each chunk first, Replace second.

    Response pipeline per chunk: chunk → AllCaps → Replace("HELLO"→"HI")
    - "hello " → "HELLO " → "HI "
    - "world" → "WORLD" → "WORLD"
    Result: "HI WORLD"
    """
    mock_anthropic.enqueue(stream_response("hello world", chunks=["hello ", "world"]))

    config = _multi_config(
        (_ALL_CAPS, {}),
        (_STRING_REPLACE, {"replacements": [["HELLO", "HI"]], "match_capitalization": False}),
    )
    async with policy_context(_MULTI_SERIAL, config, gateway_url=gateway_url, admin_api_key=admin_api_key):
        collected = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "POST",
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=auth_headers,
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
