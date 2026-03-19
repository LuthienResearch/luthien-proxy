"""Mock e2e tests for MultiParallelPolicy.

NOTE: The multi-strategy tests (designated, first_block, unanimous_pass,
most_restrictive) are not included here because MultiParallelPolicy currently
crashes with a 500 when it tries to deep-copy PolicyContext, which holds
non-copyable asyncpg objects (ReadBuffer uses Cython __cinit__).

The one test that can run without triggering that bug is the streaming rejection
test — streaming requests fail before the deep-copy happens.

When the asyncpg deep-copy bug is fixed, add the strategy-specific tests back.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d

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


@pytest.mark.asyncio
async def test_streaming_not_supported(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """MultiParallelPolicy rejects streaming requests — it needs the complete response to consolidate.

    The gateway must return a non-200 status or an SSE error event rather than
    silently hanging or returning garbage.
    """
    mock_anthropic.enqueue(text_response("hello world"))

    config = {
        "consolidation_strategy": "first_block",
        "policies": [{"class": _ALL_CAPS, "config": {}}, {"class": _NOOP, "config": {}}],
    }
    async with policy_context(_MULTI_PARALLEL, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=_HEADERS,
            )

    is_error_status = response.status_code != 200
    is_error_body = "error" in response.text
    assert is_error_status or is_error_body, (
        f"Expected error for streaming with MultiParallelPolicy, "
        f"got status={response.status_code}, body={response.text[:200]!r}"
    )
