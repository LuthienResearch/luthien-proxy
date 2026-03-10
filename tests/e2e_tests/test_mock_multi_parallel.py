"""Mock e2e tests for MultiParallelPolicy.

MultiParallelPolicy runs multiple sub-policies in parallel on independent deep
copies of the request/response and then applies a configurable consolidation
strategy to pick the winning result.

Key behaviours under test:
- "designated" strategy always uses the result from a specific sub-policy index
- "first_block" uses the first modified result; original if none modified
- "unanimous_pass" passes through only when all sub-policies agree; otherwise
  uses the first modification (same code path as first_block)
- "most_restrictive" picks the shortest (most restricted) modified output
- Streaming is NOT supported and should yield a non-200 or error response

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_multi_parallel.py -v
"""

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}

_MULTI_PARALLEL = "luthien_proxy.policies.multi_parallel_policy:MultiParallelPolicy"
_ALL_CAPS = "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
_NOOP = "luthien_proxy.policies.noop_policy:NoOpPolicy"
_STRING_REPLACE = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"


def _parallel_config(strategy: str, *policies: tuple[str, dict], designated_index: int | None = None) -> dict:
    """Build MultiParallelPolicy config from (class_ref, config) tuples.

    Args:
        strategy: Consolidation strategy ("first_block", "most_restrictive",
            "unanimous_pass", "majority_pass", "designated").
        *policies: (class_ref, config_dict) tuples for each sub-policy.
        designated_index: Required when strategy == "designated"; the index of
            the sub-policy whose result is always used.

    Returns:
        Config dict suitable for ``policy_context(_MULTI_PARALLEL, config)``.
    """
    config: dict = {
        "consolidation_strategy": strategy,
        "policies": [{"class": cls, "config": cfg} for cls, cfg in policies],
    }
    if designated_index is not None:
        config["designated_policy_index"] = designated_index
    return config


# =============================================================================
# designated strategy
# =============================================================================


@pytest.mark.xfail(
    strict=False,
    reason=(
        "MultiParallelPolicy deep-copies PolicyContext, which contains non-copyable "
        "asyncpg objects (ReadBuffer uses Cython __cinit__). Gateway returns 500. "
        "Fix: avoid deep-copying the DB connection in PolicyContext."
    ),
)
@pytest.mark.asyncio
async def test_single_policy_designated_strategy(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """MultiParallel with one AllCapsPolicy sub-policy and strategy="designated".

    The designated policy (index 0) is AllCapsPolicy, so the response text
    should be fully uppercased.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _parallel_config("designated", (_ALL_CAPS, {}), designated_index=0)
    async with policy_context(_MULTI_PARALLEL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    body = response.json()
    text = body["content"][0]["text"]
    assert text == "HELLO WORLD", f"Expected 'HELLO WORLD' from AllCapsPolicy, got: {text!r}"


# =============================================================================
# first_block strategy
# =============================================================================


@pytest.mark.xfail(
    strict=False,
    reason="MultiParallelPolicy deep-copies PolicyContext which contains non-copyable asyncpg objects.",
)
@pytest.mark.asyncio
async def test_first_block_two_policies_one_modifies(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """MultiParallel with [AllCapsPolicy, NoOpPolicy] and strategy="first_block".

    AllCaps modifies the response (uppercases it); NoOp leaves it unchanged.
    "first_block" returns the first modified result, which is AllCaps →
    "HELLO WORLD".
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _parallel_config("first_block", (_ALL_CAPS, {}), (_NOOP, {}))
    async with policy_context(_MULTI_PARALLEL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    body = response.json()
    text = body["content"][0]["text"]
    assert text == "HELLO WORLD", f"Expected 'HELLO WORLD' with first_block strategy, got: {text!r}"


# =============================================================================
# unanimous_pass strategy
# =============================================================================


@pytest.mark.xfail(
    strict=False,
    reason="MultiParallelPolicy deep-copies PolicyContext which contains non-copyable asyncpg objects.",
)
@pytest.mark.asyncio
async def test_unanimous_pass_all_pass(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """MultiParallel with [NoOpPolicy, NoOpPolicy] and strategy="unanimous_pass".

    Both policies leave the response unchanged.  "unanimous_pass" requires ALL
    sub-policies to agree to pass; since both NoOp sub-policies produce no
    modification, the original text passes through unaltered.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _parallel_config("unanimous_pass", (_NOOP, {}), (_NOOP, {}))
    async with policy_context(_MULTI_PARALLEL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    body = response.json()
    text = body["content"][0]["text"]
    assert text == "hello world", f"Expected original text to pass through unchanged, got: {text!r}"


# =============================================================================
# most_restrictive strategy
# =============================================================================


@pytest.mark.xfail(
    strict=False,
    reason="MultiParallelPolicy deep-copies PolicyContext which contains non-copyable asyncpg objects.",
)
@pytest.mark.asyncio
async def test_most_restrictive_picks_shorter(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """MultiParallel picks the shortest (most restrictive) modified result.

    Sub-policies:
      - AllCapsPolicy:            "hello world" → "HELLO WORLD"  (11 chars)
      - StringReplacementPolicy:  "hello world" → "hi"           (2 chars)

    "most_restrictive" selects the shortest output, so the result should be
    "hi" (produced by StringReplacementPolicy).
    """
    mock_anthropic.enqueue(text_response("hello world"))

    replace_config = {"replacements": [["hello world", "hi"]], "match_capitalization": False}
    config = _parallel_config(
        "most_restrictive",
        (_ALL_CAPS, {}),
        (_STRING_REPLACE, replace_config),
    )
    async with policy_context(_MULTI_PARALLEL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    body = response.json()
    text = body["content"][0]["text"]
    assert text == "hi", f"Expected 'hi' (most restrictive / shortest), got: {text!r}"


# =============================================================================
# Streaming is not supported
# =============================================================================


@pytest.mark.asyncio
async def test_streaming_raises_error(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Streaming requests with MultiParallelPolicy should fail with an error.

    MultiParallelPolicy raises NotImplementedError for all streaming hooks
    because parallel execution requires the complete response for
    consolidation.  The gateway must convert this into a non-200 status or an
    error body rather than silently hanging or returning garbage.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = _parallel_config("first_block", (_ALL_CAPS, {}), (_NOOP, {}))
    async with policy_context(_MULTI_PARALLEL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=_HEADERS,
            )

    # The gateway signals an error either via non-200 status or an SSE error event
    # (event: error / data: {"type": "error", ...}).  Both are acceptable.
    is_error_status = response.status_code != 200
    is_error_body = "error" in response.text  # catches SSE "event: error" lines

    assert is_error_status or is_error_body, (
        f"Expected error response for streaming with MultiParallelPolicy, "
        f"got status={response.status_code}, body={response.text[:200]!r}"
    )
