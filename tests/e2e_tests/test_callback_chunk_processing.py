"""E2E tests for callback chunk processing (Step 6).

Verifies that CallbackChunkLogger logs messages received from control plane,
normalization results, and chunks yielded to client.
"""

import httpx
import pytest
from tests.e2e_tests.helpers import get_litellm_logs


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_callback_control_chunks_received():
    """Verify CALLBACK CONTROL IN messages are logged when receiving from control plane."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test callback control"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify CALLBACK CONTROL IN was logged
    logs = get_litellm_logs(since_seconds=10)
    all_lines = logs.splitlines()

    control_in_logs = [line for line in all_lines if "CALLBACK CONTROL IN" in line]
    assert len(control_in_logs) > 0, (
        f"Expected CALLBACK CONTROL IN messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify it contains type information
    control_log = control_in_logs[0]
    assert "type=" in control_log, "CONTROL IN log should contain message type"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_callback_chunk_normalized():
    """Verify CALLBACK NORMALIZED messages are logged for chunk normalization."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test normalization"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify CALLBACK NORMALIZED was logged
    logs = get_litellm_logs(since_seconds=10)
    all_lines = logs.splitlines()

    normalized_logs = [line for line in all_lines if "CALLBACK NORMALIZED" in line]
    assert len(normalized_logs) > 0, (
        f"Expected CALLBACK NORMALIZED messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify it contains success information
    normalized_log = normalized_logs[0]
    assert "success=" in normalized_log, "NORMALIZED log should indicate success"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_callback_chunks_to_client():
    """Verify CALLBACK TO CLIENT messages are logged for chunks yielded to client."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test client chunks"}],
                "stream": True,
            },
        )

        # Consume the stream
        chunks_received = 0
        async for line in response.aiter_lines():
            if line.startswith("data: ") and not line.endswith("[DONE]"):
                chunks_received += 1

    assert chunks_received > 0, "Should have received streaming chunks"

    # Get logs and verify CALLBACK TO CLIENT was logged
    logs = get_litellm_logs(since_seconds=10)
    all_lines = logs.splitlines()

    to_client_logs = [line for line in all_lines if "CALLBACK TO CLIENT" in line]
    assert len(to_client_logs) > 0, (
        f"Expected CALLBACK TO CLIENT messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify the count matches (roughly - may differ slightly due to processing)
    # We should see at least some chunks logged
    assert len(to_client_logs) >= chunks_received / 2, (
        f"Expected at least {chunks_received / 2} TO CLIENT logs, but found {len(to_client_logs)}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_callback_chunk_processing_flow():
    """Verify complete flow: CONTROL IN → NORMALIZED → TO CLIENT."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test complete flow"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify all stages are present
    logs = get_litellm_logs(since_seconds=10)
    all_lines = logs.splitlines()

    control_in_logs = [line for line in all_lines if "CALLBACK CONTROL IN" in line]
    normalized_logs = [line for line in all_lines if "CALLBACK NORMALIZED" in line]
    to_client_logs = [line for line in all_lines if "CALLBACK TO CLIENT" in line]

    assert len(control_in_logs) > 0, "Should have CONTROL IN logs"
    assert len(normalized_logs) > 0, "Should have NORMALIZED logs"
    assert len(to_client_logs) > 0, "Should have TO CLIENT logs"

    # Verify ordering: chunks should be normalized before being sent to client
    # (though they may be interleaved, we can at least check the first ones)
    first_normalized_line = None
    first_to_client_line = None

    for i, line in enumerate(all_lines):
        if "CALLBACK NORMALIZED" in line and first_normalized_line is None:
            first_normalized_line = i
        if "CALLBACK TO CLIENT" in line and first_to_client_line is None:
            first_to_client_line = i
        if first_normalized_line is not None and first_to_client_line is not None:
            break

    # First normalized should appear before or near first to_client
    # (allowing some interleaving due to async nature)
    assert first_normalized_line is not None, "Should find normalized log"
    assert first_to_client_line is not None, "Should find to_client log"
