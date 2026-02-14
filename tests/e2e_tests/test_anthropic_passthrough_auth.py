"""E2E tests for Anthropic passthrough authentication.

Tests the passthrough authentication feature in three modes:

1. **x-anthropic-api-key header** (works in any auth_mode):
   Client passes their Anthropic key via x-anthropic-api-key header while
   authenticating to the proxy with PROXY_API_KEY via Authorization header.

2. **Auth mode passthrough** (requires AUTH_MODE=both or AUTH_MODE=passthrough):
   Client authenticates directly with their Anthropic key as the Bearer token.
   The proxy validates it via the free count_tokens endpoint and uses it for
   upstream API calls.

3. **OAuth passthrough via Claude Code** (requires AUTH_MODE=both):
   Claude Code uses its OAuth credentials to authenticate through the proxy
   without ANTHROPIC_API_KEY being set. The proxy validates the OAuth token
   and uses it for upstream Anthropic calls.

Prerequisites:
- Gateway must be running (docker compose up)
- ANTHROPIC_API_KEY env var for header passthrough tests
- AUTH_MODE=both for passthrough mode and OAuth tests
- Claude CLI installed + logged in via OAuth for Claude Code tests
"""

import asyncio
import json
import os

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL
from tests.e2e_tests.test_claude_code import (
    ClaudeCodeResult,
    parse_stream_json,
)

# === Fixtures ===


@pytest.fixture
def anthropic_key():
    """Require a valid Anthropic API key."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return key


@pytest.fixture
async def passthrough_mode(http_client, gateway_healthy):
    """Verify gateway supports passthrough auth mode.

    Sends a real Anthropic key as the Bearer token (not via x-anthropic-api-key).
    This only succeeds when AUTH_MODE is 'both' or 'passthrough'.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        pytest.skip("ANTHROPIC_API_KEY not set (needed for passthrough mode tests)")

    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        },
        headers={"Authorization": f"Bearer {anthropic_key}"},
    )
    if response.status_code == 401:
        pytest.skip("Gateway not in passthrough/both auth mode (set AUTH_MODE=both to enable these tests)")
    return anthropic_key


# === x-anthropic-api-key header passthrough (works in any auth_mode) ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_client_anthropic_key_non_streaming(gateway_healthy, http_client, anthropic_key):
    """Client-provided Anthropic key works for non-streaming requests."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-anthropic-api-key": anthropic_key,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_client_anthropic_key_streaming(gateway_healthy, anthropic_key):
    """Client-provided Anthropic key works for streaming requests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "claude-haiku-4-5-20241022",
                "messages": [{"role": "user", "content": "Say 'hello'"}],
                "max_tokens": 10,
                "stream": True,
            },
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "x-anthropic-api-key": anthropic_key,
            },
        ) as response:
            assert response.status_code == 200

            raw_events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    raw_events.append(line[6:])

    assert len(raw_events) > 0
    event_types = []
    for raw in raw_events:
        try:
            event_types.append(json.loads(raw).get("type"))
        except json.JSONDecodeError:
            continue
    assert "message_start" in event_types
    assert "message_stop" in event_types


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_invalid_client_key_rejected(gateway_healthy, http_client):
    """Invalid client Anthropic key is rejected by the backend."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-anthropic-api-key": "sk-ant-invalid-key-00000",
        },
    )

    assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_empty_client_key_rejected(gateway_healthy, http_client):
    """Empty x-anthropic-api-key header returns 401."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-anthropic-api-key": "",
        },
    )

    assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fallback_to_proxy_key(gateway_healthy, http_client):
    """Without x-anthropic-api-key, the proxy's own key is used."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_proxy_auth_still_required_with_client_key(gateway_healthy, http_client, anthropic_key):
    """Proxy API key (Authorization header) is required even with x-anthropic-api-key."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "x-anthropic-api-key": anthropic_key,
            # No Authorization header - should be rejected
        },
    )

    assert response.status_code == 401


# === Passthrough auth mode (requires AUTH_MODE=both or AUTH_MODE=passthrough) ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_passthrough_mode_direct_auth(http_client, passthrough_mode):
    """In passthrough/both mode, client's Anthropic key works as the Bearer token."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={"Authorization": f"Bearer {passthrough_mode}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_passthrough_mode_invalid_key_rejected(http_client, passthrough_mode):
    """In passthrough/both mode, invalid Anthropic key is rejected."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={"Authorization": "Bearer sk-ant-invalid-key-00000"},
    )

    assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_passthrough_mode_streaming(passthrough_mode):
    """In passthrough/both mode, streaming works with client's key as Bearer token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "claude-haiku-4-5-20241022",
                "messages": [{"role": "user", "content": "Say 'hello'"}],
                "max_tokens": 10,
                "stream": True,
            },
            headers={"Authorization": f"Bearer {passthrough_mode}"},
        ) as response:
            assert response.status_code == 200

            raw_events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    raw_events.append(line[6:])

    assert len(raw_events) > 0
    event_types = []
    for raw in raw_events:
        try:
            event_types.append(json.loads(raw).get("type"))
        except json.JSONDecodeError:
            continue
    assert "message_start" in event_types
    assert "message_stop" in event_types


# === OAuth passthrough via Claude Code ===


async def run_claude_code_oauth(
    prompt: str,
    gateway_url: str = GATEWAY_URL,
    max_turns: int = 1,
    timeout_seconds: int = 120,
) -> ClaudeCodeResult:
    """Run Claude Code using OAuth credentials instead of an explicit API key.

    Clears ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN from the environment
    so Claude Code falls back to its stored OAuth credentials.
    """
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    cmd.extend(["--max-turns", str(max_turns)])

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url
    # Remove explicit key env vars so Claude Code uses OAuth
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(prompt.encode()),
        timeout=timeout_seconds,
    )

    raw_output = stdout.decode()
    stderr_output = stderr.decode()
    events = parse_stream_json(raw_output)

    final_result = ""
    is_success = False
    num_turns = 0
    cost_usd = 0.0
    session_id = ""

    for event in events:
        if not event.is_result:
            continue
        final_result = event.raw.get("result", "")
        is_success = event.is_success
        num_turns = event.raw.get("num_turns", 0)
        cost_usd = event.raw.get("total_cost_usd", 0.0)
        session_id = event.raw.get("session_id", "")

    return ClaudeCodeResult(
        events=events,
        final_result=final_result,
        is_success=is_success,
        num_turns=num_turns,
        cost_usd=cost_usd,
        session_id=session_id,
        raw_output=raw_output,
        stderr=stderr_output,
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_oauth_passthrough(claude_available, passthrough_mode):
    """Claude Code authenticates through the proxy using OAuth credentials.

    Requires:
    - Gateway running with AUTH_MODE=both or AUTH_MODE=passthrough
    - Claude Code installed and logged in via OAuth (claude login)
    """
    result = await run_claude_code_oauth(
        prompt="What is 2 + 2? Reply with just the number.",
        max_turns=1,
    )

    # If Claude Code isn't logged in via OAuth, it will fail to authenticate.
    # Provide a clear message distinguishing auth failures from other issues.
    if not result.is_success:
        stderr_lower = result.stderr.lower()
        is_auth_issue = any(
            hint in stderr_lower for hint in ["not logged in", "authentication", "unauthorized", "login"]
        )
        if is_auth_issue:
            pytest.skip("Claude Code not logged in via OAuth - run 'claude login' first")

    assert result.is_success, (
        f"OAuth passthrough failed.\nstderr: {result.stderr[:500]}\nstdout: {result.raw_output[:500]}"
    )
    assert result.init_event is not None, "Should have init event"
    assert "4" in result.final_result, f"Expected '4' in result: {result.final_result}"
