"""E2E tests for policy stream instrumentation.

Verifies that PolicyStreamLogger logs CHUNK IN, CHUNK OUT, STREAM START, and STREAM END
messages for policy processing.
"""

import json
import time

import httpx
import pytest
from tests.e2e_tests.helpers import get_control_plane_logs


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_policy_stream_start_logged():
    """Verify POLICY STREAM START message is logged when policy begins processing."""
    start_time = time.time()
    call_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test policy stream start"}],
                "stream": True,
            },
        )

        # Consume the stream
        call_id = response.headers.get("x-litellm-call-id")
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            if call_id is None:
                payload = json.loads(line[6:])
                call_id = payload.get("id")

    assert call_id, "Streaming response missing call id"

    # Get logs and verify POLICY STREAM START was logged
    logs = get_control_plane_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )
    all_lines = logs.splitlines()

    start_logs = [line for line in all_lines if "POLICY STREAM START" in line]
    assert len(start_logs) > 0, (
        f"Expected POLICY STREAM START message in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify it contains stream ID and policy class
    start_log = start_logs[0]
    assert "Policy" in start_log, "START log should contain policy class name"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_policy_chunks_logged():
    """Verify POLICY CHUNK IN and CHUNK OUT messages are logged."""
    start_time = time.time()
    call_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test policy chunks"}],
                "stream": True,
            },
        )

        # Consume the stream
        call_id = response.headers.get("x-litellm-call-id")
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            if call_id is None:
                payload = json.loads(line[6:])
                call_id = payload.get("id")

    assert call_id, "Streaming response missing call id"

    # Get logs and verify POLICY CHUNK messages were logged
    logs = get_control_plane_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )
    all_lines = logs.splitlines()

    chunk_in_logs = [line for line in all_lines if "POLICY CHUNK IN" in line]
    chunk_out_logs = [line for line in all_lines if "POLICY CHUNK OUT" in line]

    assert len(chunk_in_logs) > 0, (
        f"Expected POLICY CHUNK IN messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    assert len(chunk_out_logs) > 0, (
        f"Expected POLICY CHUNK OUT messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify chunk logs contain index numbers
    assert "#0" in chunk_in_logs[0], "First CHUNK IN should have index #0"
    assert "#0" in chunk_out_logs[0], "First CHUNK OUT should have index #0"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_policy_stream_end_logged():
    """Verify POLICY STREAM END message is logged when policy completes processing."""
    start_time = time.time()
    call_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test policy stream end"}],
                "stream": True,
            },
        )

        # Consume the stream
        call_id = response.headers.get("x-litellm-call-id")
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            if call_id is None:
                payload = json.loads(line[6:])
                call_id = payload.get("id")

    assert call_id, "Streaming response missing call id"

    # Get logs and verify POLICY STREAM END was logged
    logs = get_control_plane_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )
    all_lines = logs.splitlines()

    end_logs = [line for line in all_lines if "POLICY STREAM END" in line]
    assert len(end_logs) > 0, (
        f"Expected POLICY STREAM END message in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Verify it indicates number of chunks processed
    end_log = end_logs[0]
    assert "processed" in end_log.lower(), "END log should indicate chunks processed"
    assert "chunks" in end_log.lower(), "END log should mention chunk count"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_policy_logs_use_same_stream_id():
    """Verify all policy logs for a request use the same stream ID."""
    start_time = time.time()
    call_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test policy stream id correlation"}],
                "stream": True,
            },
        )

        # Consume the stream
        call_id = response.headers.get("x-litellm-call-id")
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            if call_id is None:
                payload = json.loads(line[6:])
                call_id = payload.get("id")

    assert call_id, "Streaming response missing call id"

    # Get logs and verify all POLICY logs use the same stream ID
    logs = get_control_plane_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )
    all_lines = logs.splitlines()

    # Find the START log for our request to get the stream ID
    start_logs = [line for line in all_lines if "POLICY STREAM START" in line]
    assert len(start_logs) > 0, "Should have at least one POLICY STREAM START log"

    # Extract stream ID from the most recent START log
    import re

    uuid_pattern = re.compile(r"\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]")
    match = uuid_pattern.search(start_logs[-1])
    assert match, "POLICY START log should contain a stream ID"
    expected_stream_id = match.group(1)

    # Filter all POLICY logs for this stream ID
    this_stream_logs = [line for line in all_lines if expected_stream_id in line and "POLICY" in line]

    # Should have START, CHUNK IN, CHUNK OUT, END
    assert len(this_stream_logs) >= 4, (
        f"Expected at least 4 POLICY logs for stream {expected_stream_id}, found {len(this_stream_logs)}"
    )

    # Verify all use the same stream ID
    for log_line in this_stream_logs:
        assert expected_stream_id in log_line, (
            f"Expected all logs to contain stream ID {expected_stream_id}, but found: {log_line}"
        )
