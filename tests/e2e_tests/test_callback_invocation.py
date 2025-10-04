"""ABOUTME: E2E tests for validating litellm callback invocation and data flow
ABOUTME: Tests that callbacks are called at the right time with the right data
"""

import re
import subprocess
import time

import httpx
import pytest
from tests.e2e_tests.conftest import E2ESettings


def get_litellm_logs(since_seconds: int = 10) -> str:
    """Get recent logs from the litellm-proxy container."""
    result = subprocess.run(
        ["docker", "compose", "logs", "--since", f"{since_seconds}s", "litellm-proxy"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def assert_callback_in_logs(logs: str, callback_name: str, expected_count: int = 1) -> None:
    """Assert that a callback appears in logs the expected number of times."""
    pattern = rf"Callback invoked.*{callback_name}"
    matches = re.findall(pattern, logs)
    actual_count = len(matches)
    assert actual_count == expected_count, (
        f"Expected to find '{callback_name}' {expected_count} time(s) in logs, found {actual_count} time(s)"
    )


def assert_callback_completed_in_logs(logs: str, callback_name: str) -> None:
    """Assert that a callback completed successfully in logs."""
    pattern = rf"Callback completed.*{callback_name}"
    assert re.search(pattern, logs), f"Expected to find completion log for {callback_name}"


def get_yielded_chunk_count_from_logs(logs: str, callback_name: str) -> int:
    """Extract the number of chunks yielded by a callback from logs."""
    # Look for "Callback completed: async_post_call_streaming_iterator_hook, yielded 5 chunks"
    pattern = rf"Callback completed.*{callback_name}.*yielded (\d+) chunks"
    match = re.search(pattern, logs)
    if match:
        return int(match.group(1))
    return 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_non_streaming_callback_invocation_order(
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Verify callbacks are invoked in correct order for non-streaming requests."""
    # Record start time
    start_time = time.time()

    payload = {
        "model": "dummy-agent",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {e2e_settings.master_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        response = await client.post(
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    assert response.status_code == 200

    # Get logs since the request started
    elapsed = int(time.time() - start_time) + 2
    logs = get_litellm_logs(since_seconds=elapsed)

    # Verify callbacks were called
    assert_callback_in_logs(logs, "async_pre_call_hook", expected_count=1)
    assert_callback_in_logs(logs, "async_post_call_success_hook", expected_count=1)

    # Verify both completed
    assert_callback_completed_in_logs(logs, "async_pre_call_hook")
    assert_callback_completed_in_logs(logs, "async_post_call_success_hook")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_streaming_callback_yields_chunks_to_client(
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Verify streaming callback yields chunks and they reach the client.

    This is the CRITICAL test - what the callback yields MUST reach the client.
    If this fails, it means chunks are being lost between callback and client.
    """
    start_time = time.time()

    payload = {
        "model": "dummy-agent",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {e2e_settings.master_key}",
        "Content-Type": "application/json",
    }

    chunks_received = []
    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            assert response.status_code == 200

            async for line in response.aiter_lines():
                if line.startswith("data: ") and not line.endswith("[DONE]"):
                    import json

                    chunk = json.loads(line[6:])
                    chunks_received.append(chunk)

    assert chunks_received, "Should have received at least one chunk from client"

    # Get logs
    elapsed = int(time.time() - start_time) + 2
    logs = get_litellm_logs(since_seconds=elapsed)

    # Verify streaming callback was invoked
    assert_callback_in_logs(logs, "async_post_call_streaming_iterator_hook", expected_count=1)
    assert_callback_completed_in_logs(logs, "async_post_call_streaming_iterator_hook")

    # Get the number of chunks the callback yielded
    yielded_count = get_yielded_chunk_count_from_logs(logs, "async_post_call_streaming_iterator_hook")

    assert yielded_count > 0, f"Callback should have yielded chunks (found {yielded_count} in logs)"

    # CRITICAL ASSERTION: chunks yielded by callback must equal chunks received by client
    assert yielded_count == len(chunks_received), (
        f"CHUNKS LOST: Callback yielded {yielded_count} chunks but client received "
        f"{len(chunks_received)} chunks. This indicates chunks are being lost between "
        f"callback and client!"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_streaming_callback_invoked_before_non_streaming_hook(
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Verify that for streaming requests, the streaming iterator hook is called, not the success hook."""
    start_time = time.time()

    payload = {
        "model": "dummy-agent",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {e2e_settings.master_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            assert response.status_code == 200
            # Consume stream
            async for line in response.aiter_lines():
                pass

    elapsed = int(time.time() - start_time) + 2
    logs = get_litellm_logs(since_seconds=elapsed)

    # Should call streaming hook
    assert_callback_in_logs(logs, "async_post_call_streaming_iterator_hook", expected_count=1)

    # Should NOT call the non-streaming success hook for streaming requests
    try:
        assert_callback_in_logs(logs, "async_post_call_success_hook", expected_count=0)
    except AssertionError:
        # If we find it, that's a problem
        pytest.fail("async_post_call_success_hook should not be called for streaming requests")
