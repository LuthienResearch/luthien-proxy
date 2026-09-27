"""Mock e2e tests for the opt-in passthrough fallback (PASSTHROUGH_FALLBACK_ENABLED).

Design principle (Trello kRPRjGUx, PR #204 follow-up): the proxy should never
make things worse than direct API access. When a policy-modified request is
rejected upstream with a request-shaped 4xx, the gateway retries once with the
original unmodified request — observably (pipeline.passthrough_fallback event
+ WARNING log), and only when the policy actually changed the request.

The feature is OFF by default, and even when ON it only fires for policies
that opt in via ``passthrough_fallback_safe`` (default False), so it can never
undo a redaction or other request-side safety edit. No shipped request-rewriting
policy opts in, so these e2e tests pin the fail-closed behavior; the positive
fallback path (opted-in policy) is covered by unit tests in
tests/luthien_proxy/unit_tests/pipeline/test_anthropic_processor.py.
These tests enable the flag via the admin config API and restore afterwards.

400 errors are NOT retried by the Anthropic SDK, so a single enqueued error
maps to exactly one gateway-visible failure (no retry-slot bookkeeping needed).

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_passthrough_fallback.py -v
"""

from contextlib import asynccontextmanager

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import error_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

# StringReplacementPolicy with apply_to="request" rewrites "hello" in the
# client request before it reaches the backend: a real request-modifying
# policy of the kind used for redaction, which does NOT opt in to fallback.
_MODIFYING_POLICY_REF = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
_MODIFYING_POLICY_CONFIG = {
    "replacements": [["hello", "POLICY-REWRITTEN"]],
    "apply_to": "request",
}

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello from the client"}],
    "max_tokens": 100,
}


@asynccontextmanager
async def _passthrough_fallback_enabled(gateway_url: str, admin_api_key: str):
    """Enable PASSTHROUGH_FALLBACK_ENABLED via the admin config API; restore after.

    The flag defaults to False with source=default, so restoring = deleting the
    DB override. Skips the test if the flag is pinned by env/CLI (409).
    """
    config_url = f"{gateway_url}/api/admin/config/passthrough_fallback_enabled"
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        enable = await client.put(config_url, headers=headers, json={"value": True})
        if enable.status_code == 409:
            pytest.skip(
                "passthrough_fallback_enabled is overridden by env or CLI — cannot toggle via DB. "
                "Unset PASSTHROUGH_FALLBACK_ENABLED in the gateway's environment to run this test."
            )
        assert enable.status_code == 200, f"Failed to enable passthrough fallback: {enable.text}"
        try:
            yield
        finally:
            restore = await client.delete(config_url, headers=headers)
            assert restore.status_code == 200, f"Failed to restore passthrough fallback config: {restore.text}"


def _message_content(request_body: dict) -> str:
    return request_body["messages"][0]["content"]


@pytest.mark.asyncio
async def test_fallback_never_undoes_request_side_rewrite(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Flag ON, but StringReplacementPolicy has not opted in to fallback
    (passthrough_fallback_safe defaults to False, because request-side
    replacement is how a redaction policy works). A 400 on the rewritten
    request must propagate; the original text must never be sent upstream."""
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "modified request rejected"))

    async with _passthrough_fallback_enabled(gateway_url, admin_api_key):
        async with policy_context(
            _MODIFYING_POLICY_REF,
            _MODIFYING_POLICY_CONFIG,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
        ):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{gateway_url}/v1/messages",
                    json={**_BASE_REQUEST, "stream": False},
                    headers=auth_headers,
                )

    assert response.status_code == 400, f"Expected the 400 to propagate, got {response.status_code}: {response.text}"
    requests_seen = mock_anthropic.received_requests()
    assert len(requests_seen) == 1, f"Fallback must not resend the original; saw {len(requests_seen)} requests"
    # endswith: the gateway may prefix the first user message with the
    # <policy-context> injection (INJECT_POLICY_CONTEXT defaults to true).
    assert _message_content(requests_seen[0]).endswith("POLICY-REWRITTEN from the client")
    assert "hello" not in _message_content(requests_seen[0])


@pytest.mark.asyncio
async def test_fallback_disabled_by_default_propagates_error(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """With the flag at its default (off), the 400 from the policy-modified
    request propagates to the client and no retry is attempted."""
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "modified request rejected"))

    async with policy_context(
        _MODIFYING_POLICY_REF,
        _MODIFYING_POLICY_CONFIG,
        gateway_url=gateway_url,
        admin_api_key=admin_api_key,
    ):
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 400, f"Expected 400 with fallback off, got {response.status_code}"
    body = response.json()
    assert body.get("type") == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert len(mock_anthropic.received_requests()) == 1, "No retry should happen with fallback disabled"


@pytest.mark.asyncio
async def test_streaming_fallback_never_undoes_request_side_rewrite(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Streaming variant: flag ON, non-opted-in rewriting policy, 400 at
    stream connect. The error reaches the client and the original request is
    never sent upstream."""
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "modified request rejected"))

    async with _passthrough_fallback_enabled(gateway_url, admin_api_key):
        async with policy_context(
            _MODIFYING_POLICY_REF,
            _MODIFYING_POLICY_CONFIG,
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
        ):
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST",
                    f"{gateway_url}/v1/messages",
                    json={**_BASE_REQUEST, "stream": True},
                    headers=auth_headers,
                ) as response:
                    raw = ""
                    async for chunk in response.aiter_text():
                        raw += chunk

    # The 400 surfaces either as a pre-stream JSON error or as an SSE error
    # event, depending on whether headers were already committed.
    assert response.status_code != 200 or '"error"' in raw, (
        f"Expected the upstream 400 to reach the client, got {response.status_code}: {raw[:500]}"
    )
    requests_seen = mock_anthropic.received_requests()
    assert len(requests_seen) == 1, f"Fallback must not resend the original; saw {len(requests_seen)} requests"
    assert "hello" not in _message_content(requests_seen[0])
