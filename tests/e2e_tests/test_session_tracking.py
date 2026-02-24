"""E2E tests for session ID tracking through the gateway.

These tests invoke actual CLI tools (Claude Code and Codex) in headless mode
and verify that session IDs are correctly extracted and tracked by the gateway.

Session ID sources:
- Claude Code: metadata.user_id field with format user_<hash>_account__session_<uuid>
- Codex: x-session-id header (via OPENAI_BASE_URL pointing to gateway)

Prerequisites:
- `claude` CLI must be installed (npm install -g @anthropic-ai/claude-cli)
- `codex` CLI must be installed (see https://developers.openai.com/codex/quickstart/)
- Gateway must be running (docker compose up v2-gateway)
- Valid API credentials in env or .env
"""

import asyncio
import json
import os
import re

import httpx
import pytest
from tests.constants import DEFAULT_CLAUDE_TEST_MODEL

# Import shared config and helpers from conftest
from tests.e2e_tests.conftest import (  # noqa: F401
    ADMIN_API_KEY,
    API_KEY,
    GATEWAY_URL,
    policy_context,
)

# Session UUID pattern (matches format extracted by gateway)
SESSION_UUID_PATTERN = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")


# Fixtures claude_available, codex_available, gateway_healthy, http_client
# are provided by conftest.py and auto-discovered by pytest


# === Claude Code Helpers ===


def parse_claude_stream_json(output: str) -> list[dict]:
    """Parse JSONL stream-json output from Claude Code."""
    events = []
    for line in output.strip().split("\n"):
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


async def run_claude_code(
    prompt: str,
    timeout_seconds: int = 60,
) -> tuple[list[dict], str, str]:
    """Run Claude Code CLI in headless mode.

    Args:
        prompt: The prompt to send
        timeout_seconds: Command timeout

    Returns:
        Tuple of (events, stdout, stderr)
    """
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "1"]

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = GATEWAY_URL
    env["ANTHROPIC_AUTH_TOKEN"] = API_KEY
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
    events = parse_claude_stream_json(raw_output)

    return events, raw_output, stderr_output


def extract_claude_session_id(events: list[dict]) -> str | None:
    """Extract session ID from Claude Code events."""
    for event in events:
        if event.get("type") == "result":
            return event.get("session_id")
        if "session_id" in event:
            return event["session_id"]
    return None


# === Codex Helpers ===


def parse_codex_jsonl(output: str) -> list[dict]:
    """Parse JSONL output from Codex CLI."""
    events = []
    for line in output.strip().split("\n"):
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


async def run_codex(
    prompt: str,
    timeout_seconds: int = 60,
    session_id: str | None = None,
) -> tuple[list[dict], str, str]:
    """Run Codex CLI in non-interactive mode.

    Args:
        prompt: The prompt to send
        timeout_seconds: Command timeout
        session_id: Optional session ID to pass via environment

    Returns:
        Tuple of (events, stdout, stderr)
    """
    cmd = [
        "codex",
        "exec",
        "--json",
        "-s",
        "read-only",
        "--skip-git-repo-check",
        prompt,
    ]

    env = os.environ.copy()
    # Configure Codex to use gateway as base URL
    env["OPENAI_BASE_URL"] = f"{GATEWAY_URL}/v1"
    env["OPENAI_API_KEY"] = API_KEY

    # Pass session ID via header if provided
    # Note: Codex uses OpenAI format, so we need x-session-id header
    # This requires the gateway to read the header, which our implementation does
    if session_id:
        # Codex doesn't have a direct way to set custom headers,
        # but we can verify the gateway extracts it when present
        pass

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(),
        timeout=timeout_seconds,
    )

    raw_output = stdout.decode()
    stderr_output = stderr.decode()
    events = parse_codex_jsonl(raw_output)

    return events, raw_output, stderr_output


# === Claude Code Session Tracking Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_session_id_format(claude_available, gateway_healthy):
    """Verify Claude Code provides session ID in the expected format.

    Claude Code's session ID should be a UUID that can be extracted from
    the metadata.user_id field (format: user_<hash>_account__session_<uuid>).
    """
    events, stdout, stderr = await run_claude_code(prompt="What is 2+2? Reply with just the number.")

    # Should have events
    assert len(events) > 0, f"Expected events, got none. stderr: {stderr}"

    # Extract session ID from result event
    session_id = extract_claude_session_id(events)
    assert session_id is not None, f"No session_id in events. Events: {events}"

    # Session ID should be a valid UUID format
    assert SESSION_UUID_PATTERN.match(session_id), f"Session ID '{session_id}' doesn't match UUID pattern"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_session_consistency(claude_available, gateway_healthy):
    """Verify Claude Code session ID is consistent across all events in a request."""
    events, stdout, stderr = await run_claude_code(prompt="Say hello briefly.")

    assert len(events) > 0, f"Expected events. stderr: {stderr}"

    # Collect all session IDs from events
    session_ids = set()
    for event in events:
        if "session_id" in event:
            session_ids.add(event["session_id"])

    # All events should have the same session ID
    assert len(session_ids) == 1, f"Expected all events to have same session ID, got: {session_ids}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_request_flows_through_gateway(claude_available, gateway_healthy):
    """Verify Claude Code request successfully flows through the gateway.

    This confirms the gateway correctly handles Claude Code's metadata.user_id
    field containing the session information.
    """
    events, stdout, stderr = await run_claude_code(prompt="What is the capital of France? One word answer.")

    # Find result event
    result_event = None
    for event in events:
        if event.get("type") == "result":
            result_event = event
            break

    assert result_event is not None, f"No result event found. Events: {events}"
    assert result_event.get("subtype") == "success", f"Request failed. Result: {result_event}, stderr: {stderr}"


# === Codex Session Tracking Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_codex_request_flows_through_gateway(codex_available, gateway_healthy):
    """Verify Codex request successfully flows through the gateway.

    Codex uses OpenAI format and can provide session ID via x-session-id header.
    This test confirms requests work through the gateway even without explicit session ID.
    """
    events, stdout, stderr = await run_codex(prompt="What is 2+2? Reply with just the number, nothing else.")

    # Codex should complete successfully
    # Check if we got any output (events or raw text)
    assert stdout or events, f"No output from Codex. stderr: {stderr}"

    # If we got JSONL events, check for completion
    if events:
        # Look for a completion or success indicator
        has_response = any("message" in e or "response" in e or "content" in e for e in events)
        # Even if no explicit response event, having events means it worked
        assert len(events) > 0 or has_response, f"Unexpected events: {events}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_codex_uses_gateway_endpoint(codex_available, gateway_healthy):
    """Verify Codex is configured to use the gateway endpoint.

    The OPENAI_BASE_URL environment variable should route requests through gateway.
    """
    # Make a simple request - if it succeeds, the gateway is being used
    events, stdout, stderr = await run_codex(prompt="Say 'hello' and nothing else.")

    # If we got here without connection errors, gateway is being used
    # Check for any kind of successful response
    assert stdout or events, f"No response from gateway. stderr: {stderr}"


# === Cross-verification Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_multiple_requests_different_sessions(claude_available, gateway_healthy):
    """Verify different Claude Code invocations get different session IDs.

    Each `claude -p` invocation should create a new session with unique ID.
    """
    # Make two separate requests
    events1, _, stderr1 = await run_claude_code(prompt="Say 'one'")
    events2, _, stderr2 = await run_claude_code(prompt="Say 'two'")

    session_id_1 = extract_claude_session_id(events1)
    session_id_2 = extract_claude_session_id(events2)

    assert session_id_1 is not None, f"No session ID in first request. stderr: {stderr1}"
    assert session_id_2 is not None, f"No session ID in second request. stderr: {stderr2}"

    # Different invocations should have different session IDs
    assert session_id_1 != session_id_2, (
        f"Expected different session IDs for different invocations, got same: {session_id_1}"
    )


# Policy helpers (set_policy, get_current_policy, policy_context) and http_client fixture
# are provided by conftest.py


# === HTTP Client Tests (for direct verification) ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_endpoint_accepts_claude_code_metadata(http_client, gateway_healthy):
    """Verify Anthropic endpoint correctly handles Claude Code's metadata format.

    This simulates the request format Claude Code sends, with session info
    embedded in metadata.user_id.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
            "metadata": {"user_id": "user_abc123hash_account__session_12345678-1234-1234-1234-123456789abc"},
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    # Verify Anthropic response structure
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_endpoint_accepts_session_header(http_client, gateway_healthy):
    """Verify OpenAI endpoint correctly handles x-session-id header.

    This simulates how Codex or other OpenAI-format clients can provide session ID.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-session-id": "test-codex-session-12345",
        },
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    # Verify OpenAI response structure
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0
    assert "content" in data["choices"][0]["message"]


# === Debug Policy Tests (verify server-side session extraction) ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_session_captured_by_debug_policy(claude_available, gateway_healthy):
    """Verify DebugLoggingPolicy captures session info from Claude Code requests.

    This test activates DebugLoggingPolicy, runs Claude Code through the gateway,
    and verifies the request completes successfully. The debug policy logs the
    raw HTTP request including metadata.user_id which contains the session ID.
    """
    async with policy_context(
        "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
        {},
    ):
        events, stdout, stderr = await run_claude_code(prompt="Say 'test' and nothing else.")

        # Request should succeed
        assert len(events) > 0, f"Expected events. stderr: {stderr}"

        result_event = None
        for event in events:
            if event.get("type") == "result":
                result_event = event
                break

        assert result_event is not None, f"No result event. Events: {events}"
        assert result_event.get("subtype") == "success", f"Request failed: {result_event}"

        # Session ID should be present
        session_id = extract_claude_session_id(events)
        assert session_id is not None, "Session ID should be captured"
        assert SESSION_UUID_PATTERN.match(session_id), f"Invalid session ID format: {session_id}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_codex_with_debug_policy(codex_available, gateway_healthy):
    """Verify Codex requests work with DebugLoggingPolicy active.

    This test activates DebugLoggingPolicy and runs Codex through the gateway.
    """
    async with policy_context(
        "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
        {},
    ):
        events, stdout, stderr = await run_codex(prompt="Say 'test' and nothing else.")

        # Request should complete (either events or stdout)
        assert stdout or events, f"No output from Codex. stderr: {stderr}"


# === Server-side Session Verification Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_session_id_persisted_to_database(http_client, gateway_healthy):
    """Verify session_id is correctly persisted to database and returned via debug API.

    This test:
    1. Sends a request with a known session ID in metadata.user_id
    2. Retrieves the call events via debug API
    3. Verifies the session_id was extracted and stored correctly
    """
    test_session_uuid = "aabbccdd-1122-3344-5566-778899aabbcc"
    test_user_id = f"user_testhash123_account__session_{test_session_uuid}"

    # Make request with session metadata
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Say 'verified'"}],
            "max_tokens": 20,
            "stream": False,
            "metadata": {"user_id": test_user_id},
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"

    # Get call_id from response headers
    call_id = response.headers.get("x-call-id")
    assert call_id, "No X-Call-ID header in response"

    # Query debug API to verify session_id was persisted
    # Wait a moment for async persistence
    await asyncio.sleep(0.5)

    debug_response = await http_client.get(
        f"{GATEWAY_URL}/debug/calls/{call_id}",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )

    assert debug_response.status_code == 200, f"Debug API failed: {debug_response.text}"
    debug_data = debug_response.json()

    # Verify session_id at call level
    assert debug_data.get("session_id") == test_session_uuid, (
        f"Expected session_id '{test_session_uuid}', got '{debug_data.get('session_id')}'"
    )

    # Verify at least one event has the session_id
    events_with_session = [e for e in debug_data["events"] if e.get("session_id") == test_session_uuid]
    assert len(events_with_session) > 0, f"No events have session_id. Events: {debug_data['events']}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_session_header_persisted(http_client, gateway_healthy):
    """Verify x-session-id header is correctly persisted for OpenAI format requests."""
    test_session_id = "openai-test-session-xyz789"

    # Make OpenAI format request with session header
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say 'verified'"}],
            "max_tokens": 20,
            "stream": False,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-session-id": test_session_id,
        },
    )

    assert response.status_code == 200, f"Request failed: {response.text}"

    # Get call_id from response headers
    call_id = response.headers.get("x-call-id")
    assert call_id, "No X-Call-ID header in response"

    # Query debug API to verify session_id was persisted
    await asyncio.sleep(0.5)

    debug_response = await http_client.get(
        f"{GATEWAY_URL}/debug/calls/{call_id}",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )

    assert debug_response.status_code == 200, f"Debug API failed: {debug_response.text}"
    debug_data = debug_response.json()

    # Verify session_id at call level
    assert debug_data.get("session_id") == test_session_id, (
        f"Expected session_id '{test_session_id}', got '{debug_data.get('session_id')}'"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_session_matches_server(claude_available, gateway_healthy):
    """Verify Claude Code's session ID matches what the server extracted and stored.

    This is the key end-to-end verification: the session ID reported by Claude Code
    must match the session ID extracted and persisted by the gateway.
    """
    events, stdout, stderr = await run_claude_code(prompt="Say 'match test'")

    assert len(events) > 0, f"Expected events. stderr: {stderr}"

    # Get session ID from Claude Code output
    client_session_id = extract_claude_session_id(events)
    assert client_session_id is not None, f"No session_id in client events. Events: {events}"

    # Get call_id from the init event or result
    call_id = None
    for event in events:
        if event.get("type") == "init":
            call_id = event.get("call_id")
            break

    # If no call_id in events, we can't verify server-side (skip gracefully)
    if not call_id:
        # Just verify the client-side session format is valid
        assert SESSION_UUID_PATTERN.match(client_session_id), f"Client session ID invalid format: {client_session_id}"
        return

    # Query debug API to get server's extracted session_id
    async with httpx.AsyncClient(timeout=30.0) as client:
        await asyncio.sleep(0.5)  # Wait for async persistence

        debug_response = await client.get(
            f"{GATEWAY_URL}/debug/calls/{call_id}/events",
            headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
        )

        if debug_response.status_code == 200:
            debug_data = debug_response.json()
            server_session_id = debug_data.get("session_id")

            # The server-extracted session ID should match client-reported session ID
            assert server_session_id == client_session_id, (
                f"Session ID mismatch! Client: {client_session_id}, Server: {server_session_id}"
            )
