"""E2E tests for Anthropic passthrough authentication.

Covers two passthrough paths:

1. **x-anthropic-api-key header** (works in any auth_mode):
   Client authenticates to the proxy with CLIENT_API_KEY via Authorization,
   and forwards an Anthropic API key for upstream calls via x-anthropic-api-key.

2. **OAuth passthrough via Claude Code** (requires AUTH_MODE=both or passthrough):
   Claude Code uses its OAuth credentials as the Bearer token. The proxy
   validates them and uses them for upstream Anthropic calls.

Direct API-key-as-Bearer tests are deliberately *not* covered here: Anthropic
only accepts API keys via x-api-key, and Bearer tokens are reserved for OAuth
credentials whose automated use Anthropic forbids. The OAuth path is therefore
exercised only via Claude Code, never via raw HTTP calls from this suite.

Prerequisites:
- Gateway must be running (docker compose up)
- ANTHROPIC_API_KEY env var for header passthrough tests
- AUTH_MODE=both (or passthrough) on the gateway for the Claude Code OAuth test
- Claude CLI installed + logged in via OAuth for Claude Code tests
"""

import asyncio
import json
import os

import httpx
import pytest
from tests.constants import DEFAULT_TEST_MODEL
from tests.luthien_proxy.e2e_tests.test_claude_code import (
    ClaudeCodeResult,
    parse_stream_json,
)

ANTHROPIC_MODEL = os.environ.get("E2E_ANTHROPIC_MODEL", DEFAULT_TEST_MODEL)

# === Fixtures ===


@pytest.fixture
def anthropic_key():
    """Require a valid Anthropic API key."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return key


@pytest.fixture
async def gateway_passthrough_mode(http_client, gateway_healthy, gateway_url, admin_api_key):
    """Confirm the gateway is in an auth_mode that accepts passthrough (both or passthrough).

    Reads auth_config from the admin API instead of probing /v1/messages with a
    bearer token — Anthropic forbids automated use of OAuth credentials, so we
    must not exercise OAuth via direct API tests. Tests that need a real bearer
    flow run Claude Code instead (see ``test_claude_code_oauth_passthrough``).
    """
    response = await http_client.get(
        f"{gateway_url}/api/admin/auth/config",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    # Surface server-side regressions; only treat genuine auth/availability
    # responses as skip-worthy prereq misses.
    if response.status_code >= 500:
        response.raise_for_status()
    if response.status_code in (401, 403, 404):
        pytest.skip(f"Admin auth config not accessible: HTTP {response.status_code}")
    assert response.status_code == 200, f"Unexpected status reading admin auth config: {response.status_code}"
    mode = response.json().get("auth_mode")
    if mode not in ("both", "passthrough"):
        pytest.skip(f"Gateway auth_mode={mode!r}; passthrough tests require 'both' or 'passthrough'")
    yield


# === x-anthropic-api-key header passthrough (works in any auth_mode) ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_client_anthropic_key_non_streaming(gateway_healthy, http_client, anthropic_key, gateway_url, api_key):
    """Client-provided Anthropic key works for non-streaming requests."""
    response = await http_client.post(
        f"{gateway_url}/v1/messages",
        json={
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "x-anthropic-api-key": anthropic_key,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_client_anthropic_key_streaming(gateway_healthy, anthropic_key, gateway_url, api_key):
    """Client-provided Anthropic key works for streaming requests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{gateway_url}/v1/messages",
            json={
                "model": ANTHROPIC_MODEL,
                "messages": [{"role": "user", "content": "Say 'hello'"}],
                "max_tokens": 10,
                "stream": True,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
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
async def test_invalid_client_key_rejected(gateway_healthy, http_client, gateway_url, api_key):
    """Invalid client Anthropic key is rejected by the backend."""
    response = await http_client.post(
        f"{gateway_url}/v1/messages",
        json={
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "x-anthropic-api-key": "sk-ant-invalid-key-00000",
        },
    )

    assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_empty_client_key_rejected(gateway_healthy, http_client, gateway_url, api_key):
    """Empty x-anthropic-api-key header returns 401."""
    response = await http_client.post(
        f"{gateway_url}/v1/messages",
        json={
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "x-anthropic-api-key": "",
        },
    )

    assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fallback_to_client_key(gateway_healthy, http_client, gateway_url, api_key):
    """Without x-anthropic-api-key, the proxy's own key is used."""
    response = await http_client.post(
        f"{gateway_url}/v1/messages",
        json={
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_proxy_auth_still_required_with_client_key(gateway_healthy, http_client, anthropic_key, gateway_url):
    """Proxy API key (Authorization header) is required even with x-anthropic-api-key."""
    response = await http_client.post(
        f"{gateway_url}/v1/messages",
        json={
            "model": ANTHROPIC_MODEL,
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "x-anthropic-api-key": anthropic_key,
            # No Authorization header - should be rejected
        },
    )

    assert response.status_code == 401


# === Passthrough auth mode ===
#
# Note: Bearer-token passthrough only legitimately exercises OAuth credentials
# (sk-ant- API keys must use x-api-key per Anthropic's API). Anthropic forbids
# automated use of OAuth tokens, so OAuth flows are tested through Claude Code
# below — never by direct API calls from this suite.


# === OAuth passthrough via Claude Code ===


async def run_claude_code_oauth(
    prompt: str,
    gateway_url: str,
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
    # Allow nested Claude Code sessions when running from within Claude Code
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

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
async def test_claude_code_oauth_passthrough(claude_available, gateway_passthrough_mode, gateway_url):
    """Claude Code authenticates through the proxy using OAuth credentials.

    Requires:
    - Gateway running with AUTH_MODE=both or AUTH_MODE=passthrough
    - Claude Code installed and logged in via OAuth (claude login)
    """
    result = await run_claude_code_oauth(
        prompt="What is 2 + 2? Reply with just the number.",
        gateway_url=gateway_url,
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
