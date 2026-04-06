"""Mock e2e tests for LLM02: DeSlop Policy 400 Error.

Verify DeSlop-style policies (NoYappingPolicy, NoApologiesPolicy, PlainDashesPolicy)
activate and serve requests without returning 400 errors.
Trello: https://trello.com/c/99Zgysbl/1099

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_llm02_deslop_policy.py -v
"""

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import (
    BASE_REQUEST,
    collect_sse_text,
    judge_pass,
    judge_replace_text,
    policy_context,
)
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator

pytestmark = [pytest.mark.mock_e2e, pytest.mark.uat_deslop]

_NO_YAPPING = "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy"
_NO_APOLOGIES = "luthien_proxy.policies.presets.no_apologies:NoApologiesPolicy"
_PLAIN_DASHES = "luthien_proxy.policies.presets.plain_dashes:PlainDashesPolicy"

_SLOPPY_RESPONSE = (
    "Certainly! I'd be absolutely happy to help you with that. Of course, let me explain this in great detail..."
)


@pytest.mark.asyncio
async def test_no_yapping_activates_without_400(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """NoYappingPolicy activates and serves a non-streaming request without 400."""
    mock_anthropic.enqueue(text_response("Here is the answer."))
    mock_anthropic.enqueue(judge_pass())

    async with policy_context(_NO_YAPPING, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    assert response.json()["type"] == "message"


@pytest.mark.asyncio
async def test_no_yapping_strips_filler(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """NoYappingPolicy judge replaces filler-heavy response with a concise version.

    Uses ClaudeCodeSimulator (api_key) instead of raw httpx (auth_headers) because
    the simulator handles conversation state and header construction internally.
    """
    mock_anthropic.enqueue(text_response(_SLOPPY_RESPONSE))
    mock_anthropic.enqueue(judge_replace_text("Here is the answer."))

    async with policy_context(_NO_YAPPING, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn = await session.send("Explain something")

    assert "Here is the answer." in turn.text
    assert "Certainly" not in turn.text


@pytest.mark.asyncio
async def test_no_yapping_streaming_no_400(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """NoYappingPolicy streaming request does not return 400."""
    mock_anthropic.enqueue(text_response("Concise answer."))
    mock_anthropic.enqueue(judge_pass())

    async with policy_context(_NO_YAPPING, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": True},
                headers=auth_headers,
            ) as response:
                assert response.status_code == 200, f"Expected 200, got {response.status_code}"
                text = await collect_sse_text(response)

    assert "Concise answer." in text


@pytest.mark.asyncio
async def test_no_apologies_activates_without_400(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """NoApologiesPolicy activates and serves a request without 400."""
    mock_anthropic.enqueue(text_response("Here is the answer."))
    mock_anthropic.enqueue(judge_pass())

    async with policy_context(_NO_APOLOGIES, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    assert response.json()["type"] == "message"


@pytest.mark.asyncio
async def test_plain_dashes_replaces_em_dashes(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """PlainDashesPolicy replaces em-dashes with plain ASCII dashes without 400."""
    mock_anthropic.enqueue(text_response("Here \u2014 is the answer."))
    mock_anthropic.enqueue(judge_replace_text("Here - is the answer."))

    async with policy_context(_PLAIN_DASHES, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    assert response.json()["type"] == "message"
    body = response.json()
    content_text = " ".join(b["text"] for b in body["content"] if b["type"] == "text")
    assert "\u2014" not in content_text, f"Em-dash should have been replaced, got: {content_text!r}"
    assert "Here - is the answer." in content_text
