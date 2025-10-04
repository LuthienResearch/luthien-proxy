"""E2E tests for control plane endpoint logging in streaming pipeline."""

import httpx
import pytest
from tests.e2e_tests.helpers import (
    get_control_plane_logs,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_endpoint_start_message_logged():
    """Verify ENDPOINT START message is logged when stream begins."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test endpoint start"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify ENDPOINT START was logged
    logs = get_control_plane_logs(since_seconds=10)
    all_lines = logs.splitlines()

    start_logs = [line for line in all_lines if "ENDPOINT START" in line]
    assert len(start_logs) > 0, (
        f"Expected ENDPOINT START message in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify it contains key fields
    start_log = start_logs[0]
    assert "call_id=" in start_log, "START log should contain call_id"
    assert "model=dummy-agent" in start_log, "START log should contain model name"
    assert "stream=True" in start_log, "START log should indicate streaming"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_endpoint_policy_invocation_logged():
    """Verify ENDPOINT POLICY message is logged when policy is invoked."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test policy invocation"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify ENDPOINT POLICY was logged
    logs = get_control_plane_logs(since_seconds=10)
    all_lines = logs.splitlines()

    policy_logs = [line for line in all_lines if "ENDPOINT POLICY" in line]
    assert len(policy_logs) > 0, (
        f"Expected ENDPOINT POLICY message in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify it contains policy class name
    policy_log = policy_logs[0]
    assert "invoking" in policy_log, "POLICY log should mention 'invoking'"
    assert "Policy" in policy_log, "POLICY log should contain policy class name"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_endpoint_chunks_logged():
    """Verify ENDPOINT CHUNK IN and CHUNK OUT messages are logged."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test chunks"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify ENDPOINT CHUNK messages were logged
    logs = get_control_plane_logs(since_seconds=10)
    all_lines = logs.splitlines()

    chunk_in_logs = [line for line in all_lines if "ENDPOINT CHUNK IN" in line]
    chunk_out_logs = [line for line in all_lines if "ENDPOINT CHUNK OUT" in line]

    assert len(chunk_in_logs) > 0, (
        f"Expected ENDPOINT CHUNK IN messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    assert len(chunk_out_logs) > 0, (
        f"Expected ENDPOINT CHUNK OUT messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify chunk logs contain index numbers
    assert "#0" in chunk_in_logs[0], "First CHUNK IN should have index #0"
    assert "#0" in chunk_out_logs[0], "First CHUNK OUT should have index #0"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_endpoint_end_message_logged():
    """Verify ENDPOINT END message is logged when stream completes."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test endpoint end"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify ENDPOINT END was logged
    logs = get_control_plane_logs(since_seconds=10)
    all_lines = logs.splitlines()

    end_logs = [line for line in all_lines if "ENDPOINT END" in line]
    assert len(end_logs) > 0, (
        f"Expected ENDPOINT END message in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify it indicates stream completion
    end_log = end_logs[0]
    assert "stream complete" in end_log, "END log should indicate stream completion"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_endpoint_logs_use_same_stream_id():
    """Verify all endpoint logs for a request use the same stream ID."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test stream id correlation"}],
                "stream": True,
            },
        )

        # Consume the stream
        async for line in response.aiter_lines():
            pass

    # Get logs and verify all ENDPOINT logs use the same stream ID
    logs = get_control_plane_logs(since_seconds=5)
    all_lines = logs.splitlines()

    # Find the START log for our request to get the stream ID
    start_logs = [line for line in all_lines if "ENDPOINT START" in line]
    assert len(start_logs) > 0, "Should have at least one START log"

    # Extract stream ID from the most recent START log
    import re

    uuid_pattern = re.compile(r"\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]")
    match = uuid_pattern.search(start_logs[-1])
    assert match, "START log should contain a stream ID"
    expected_stream_id = match.group(1)

    # Filter all ENDPOINT logs for this stream ID
    this_stream_logs = [line for line in all_lines if expected_stream_id in line and "ENDPOINT" in line]

    # Should have START, POLICY, CHUNK IN, CHUNK OUT, END
    assert len(this_stream_logs) >= 4, (
        f"Expected at least 4 ENDPOINT logs for stream {expected_stream_id}, found {len(this_stream_logs)}"
    )

    # Verify all use the same stream ID
    for log_line in this_stream_logs:
        assert expected_stream_id in log_line, (
            f"Expected all logs to contain stream ID {expected_stream_id}, but found: {log_line}"
        )
