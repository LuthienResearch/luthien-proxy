"""Mock e2e tests for MultiBackendPolicy.

Covers the full request path: client → gateway → policy → N parallel Anthropic
calls (all hitting the mock server) → aggregated response back to the client.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_multi_backend.py -v
"""

from __future__ import annotations

import json

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import auth_config_context, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_POLICY_REF = "luthien_proxy.policies.multi_backend_policy:MultiBackendPolicy"
_MODELS = ["model-alpha", "model-beta", "model-gamma"]

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",  # overridden by the policy
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}


@pytest.mark.asyncio
async def test_multi_backend_non_streaming_aggregates_all_models(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
    admin_api_key: str,
):
    """Non-streaming: every configured model appears as a labeled section."""
    # Three parallel calls will drain three queued responses. Content differs
    # per response so we can verify all three made it into the aggregate.
    mock_anthropic.enqueue(text_response("alpha-says-hello"))
    mock_anthropic.enqueue(text_response("beta-says-howdy"))
    mock_anthropic.enqueue(text_response("gamma-says-greetings"))

    async with (
        auth_config_context("passthrough", gateway_url=gateway_url, admin_api_key=admin_api_key),
        policy_context(
            _POLICY_REF,
            {"models": _MODELS},
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
        ),
    ):
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["type"] == "message"
    # Aggregated model label encodes all fan-out targets.
    assert data["model"] == "multi[model-alpha,model-beta,model-gamma]"

    joined = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
    for model_name in _MODELS:
        assert f"# {model_name}" in joined, f"missing label for {model_name}"
    # Each mock response's text should appear somewhere — exactly which model
    # got which is nondeterministic (FIFO drain races with fan-out).
    for expected in ("alpha-says-hello", "beta-says-howdy", "gamma-says-greetings"):
        assert expected in joined, f"missing content: {expected}"


@pytest.mark.asyncio
async def test_multi_backend_streaming_emits_labeled_sections(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
    admin_api_key: str,
):
    """Streaming: one message_start/stop pair framing per-model labeled blocks."""
    mock_anthropic.enqueue(text_response("alpha-content"))
    mock_anthropic.enqueue(text_response("beta-content"))

    events_seen: list[str] = []
    text_deltas: list[str] = []
    async with (
        auth_config_context("passthrough", gateway_url=gateway_url, admin_api_key=admin_api_key),
        policy_context(
            _POLICY_REF,
            {"models": ["model-alpha", "model-beta"]},
            gateway_url=gateway_url,
            admin_api_key=admin_api_key,
        ),
    ):
        async with httpx.AsyncClient(timeout=20.0) as client:
            async with client.stream(
                "POST",
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=auth_headers,
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")

                current_event: str | None = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                        events_seen.append(current_event)
                    elif line.startswith("data:") and current_event == "content_block_delta":
                        try:
                            data = json.loads(line[len("data:") :].strip())
                        except json.JSONDecodeError:
                            continue
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_deltas.append(delta.get("text", ""))

    # Framing: exactly one message_start and one message_stop surrounding
    # multiple content blocks.
    assert events_seen.count("message_start") == 1
    assert events_seen.count("message_stop") == 1
    assert events_seen[0] == "message_start"
    assert events_seen[-1] == "message_stop"
    assert events_seen.count("content_block_start") == events_seen.count("content_block_stop")
    # Each model produces a labeled section preceded by a header block,
    # so >= 4 content_block_start/stop pairs (2 headers + >=2 content blocks).
    assert events_seen.count("content_block_start") >= 4

    joined = "".join(text_deltas)
    assert "# model-alpha" in joined
    assert "# model-beta" in joined
    assert "alpha-content" in joined
    assert "beta-content" in joined
