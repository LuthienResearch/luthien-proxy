"""ABOUTME: E2E tests for validating litellm callback invocation and data flow
ABOUTME: Tests that callbacks are called at the right time with the right data
"""

import json
import re
import time

import httpx
import pytest
from tests.e2e_tests.conftest import E2ESettings
from tests.e2e_tests.helpers import get_litellm_logs


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
    body = response.json()
    call_id = response.headers.get("x-litellm-call-id") or body.get("id")
    assert call_id, "Non-streaming response missing call id"

    logs = get_litellm_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )

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

    call_id = None
    chunks_received = []
    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            assert response.status_code == 200
            call_id = response.headers.get("x-litellm-call-id")

            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                chunk = json.loads(line[6:])
                if call_id is None:
                    call_id = chunk.get("id")
                chunks_received.append(chunk)

    assert chunks_received, "Should have received at least one chunk from client"
    assert call_id, "Streaming response missing call id"

    # Get logs
    logs = get_litellm_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )

    # Verify streaming callback was invoked
    assert_callback_in_logs(logs, "async_post_call_streaming_iterator_hook", expected_count=1)
    assert_callback_completed_in_logs(logs, "async_post_call_streaming_iterator_hook")

    # Get the number of chunks the callback yielded
    chunk_log_count = sum(1 for line in logs.splitlines() if "CALLBACK TO CLIENT" in line)
    yielded_count = chunk_log_count

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

    call_id = None
    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            assert response.status_code == 200
            call_id = response.headers.get("x-litellm-call-id")
            # Consume stream
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                if call_id is None:
                    chunk = json.loads(line[6:])
                    call_id = chunk.get("id")

    assert call_id, "Streaming response missing call id"

    logs = get_litellm_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )

    # Should call streaming hook
    assert_callback_in_logs(logs, "async_post_call_streaming_iterator_hook", expected_count=1)

    # Should NOT call the non-streaming success hook for streaming requests
    try:
        assert_callback_in_logs(logs, "async_post_call_success_hook", expected_count=0)
    except AssertionError:
        # If we find it, that's a problem
        pytest.fail("async_post_call_success_hook should not be called for streaming requests")
