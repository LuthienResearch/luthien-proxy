"""Mock e2e tests for policy behavior — deterministic assertions without real API calls.

Unlike the real e2e policy tests which can only check "did it crash?", these tests
control the mock response exactly, enabling precise assertions on transformed output.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d
  - Mock server auto-started by the mock_anthropic fixture (port 18888).

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_policies.py -v
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

# All policies with minimal configs — used for smoke tests
_ALL_POLICIES = [
    pytest.param("luthien_proxy.policies.noop_policy:NoOpPolicy", {}, id="NoOpPolicy"),
    pytest.param("luthien_proxy.policies.all_caps_policy:AllCapsPolicy", {}, id="AllCapsPolicy"),
    pytest.param("luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy", {}, id="DebugLoggingPolicy"),
    pytest.param(
        "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        {"replacements": [["hello", "hi"]]},
        id="StringReplacementPolicy",
    ),
]


# =============================================================================
# Smoke tests — every policy should handle requests without crashing
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_class_ref,config", _ALL_POLICIES)
async def test_policy_non_streaming_smoke(
    policy_class_ref: str,
    config: dict,
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Every policy returns 200 for a non-streaming Anthropic request."""
    mock_anthropic.enqueue(text_response("hello world"))

    async with policy_context(policy_class_ref, config):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200, f"{policy_class_ref} failed: {response.text}"
    data = response.json()
    assert data["type"] == "message"
    assert len(data["content"]) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_class_ref,config", _ALL_POLICIES)
async def test_policy_streaming_smoke(
    policy_class_ref: str,
    config: dict,
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Every policy returns a valid SSE stream for a streaming Anthropic request."""
    mock_anthropic.enqueue(stream_response("hello world"))

    async with policy_context(policy_class_ref, config):
        events_seen = set()
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "POST",
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=_HEADERS,
            ) as response:
                assert response.status_code == 200, f"{policy_class_ref} failed: {response.status_code}"
                assert "text/event-stream" in response.headers.get("content-type", "")

                current_event = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                        events_seen.add(current_event)

    required = {
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    }
    missing = required - events_seen
    assert not missing, f"{policy_class_ref}: missing SSE events {missing}"


# =============================================================================
# AllCapsPolicy — verify exact output transformation
# =============================================================================


@pytest.mark.asyncio
async def test_all_caps_non_streaming(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """AllCapsPolicy uppercases response text in non-streaming mode."""
    mock_anthropic.enqueue(text_response("hello from the assistant"))

    async with policy_context("luthien_proxy.policies.all_caps_policy:AllCapsPolicy", {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    assert text == "HELLO FROM THE ASSISTANT"


@pytest.mark.asyncio
async def test_all_caps_streaming(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """AllCapsPolicy uppercases each streaming text chunk."""
    mock_anthropic.enqueue(stream_response("hello world", chunks=["hello ", "world"]))

    async with policy_context("luthien_proxy.policies.all_caps_policy:AllCapsPolicy", {}):
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

    assert "".join(collected) == "HELLO WORLD"


# =============================================================================
# StringReplacementPolicy — verify exact substitution
# =============================================================================


@pytest.mark.asyncio
async def test_string_replacement_non_streaming(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """StringReplacementPolicy substitutes the target word in a non-streaming response."""
    mock_anthropic.enqueue(text_response("Anthropic makes great models"))

    async with policy_context(
        "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        {"replacements": [["Anthropic", "ACME Corp"]], "match_capitalization": False},
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    assert "Anthropic" not in text
    assert "ACME Corp" in text
    assert text == "ACME Corp makes great models"


@pytest.mark.asyncio
async def test_string_replacement_capitalization_preserved(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """StringReplacementPolicy preserves source capitalization in the replacement."""
    # "anthropic" (lower) -> "acme corp", "ANTHROPIC" (upper) -> "ACME CORP", "Anthropic" (title) -> "Acme corp"
    mock_anthropic.enqueue(text_response("Anthropic and ANTHROPIC and anthropic"))

    async with policy_context(
        "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        {"replacements": [["anthropic", "acme corp"]], "match_capitalization": True},
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    assert response.status_code == 200
    text = response.json()["content"][0]["text"]
    assert "Anthropic" not in text
    assert "ANTHROPIC" not in text
    assert "anthropic" not in text
    # Title -> "Acme corp", UPPER -> "ACME CORP", lower -> "acme corp"
    assert "Acme corp" in text
    assert "ACME CORP" in text
    assert "acme corp" in text


@pytest.mark.asyncio
async def test_string_replacement_streaming(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """StringReplacementPolicy substitutes target word across streaming chunks."""
    # Put the replacement target in a single chunk so it's not split across boundaries
    mock_anthropic.enqueue(stream_response("hello world", chunks=["Anthropic ", "makes ", "models"]))

    async with policy_context(
        "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        {"replacements": [["Anthropic", "ACME Corp"]], "match_capitalization": False},
    ):
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
    assert "Anthropic" not in full_text
    assert "ACME Corp" in full_text


@pytest.mark.asyncio
async def test_string_replacement_streaming_complete_sse_events(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """StringReplacementPolicy preserves all required SSE event types including message_delta.

    Regression test for a bug where StringReplacementPolicy dropped the finish_reason,
    causing message_delta and message_stop to be missing from the stream.
    """
    mock_anthropic.enqueue(stream_response("some text to replace"))

    async with policy_context(
        "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        {"replacements": [["some", "any"]], "match_capitalization": False},
    ):
        events_seen = set()
        stop_reason = None

        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "POST",
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=_HEADERS,
            ) as response:
                assert response.status_code == 200

                current_event = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                        events_seen.add(current_event)
                    elif line.startswith("data:") and current_event == "message_delta":
                        try:
                            data = json.loads(line[len("data:") :].strip())
                            stop_reason = data.get("delta", {}).get("stop_reason")
                        except json.JSONDecodeError:
                            pass

    required = {
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    }
    missing = required - events_seen
    assert not missing, f"Missing SSE events: {missing}"
    assert stop_reason is not None, "message_delta missing stop_reason — policy may have dropped finish_reason"
