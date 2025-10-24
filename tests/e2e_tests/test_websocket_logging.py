"""E2E tests for WebSocket message logging in streaming pipeline."""

import json
import re
import time

import httpx
import pytest
from tests.e2e_tests.helpers.callback_assertions import get_litellm_logs


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_websocket_outgoing_messages_logged():
    """Verify WebSocket OUT messages (litellm → control plane) are logged."""
    # Make a streaming request to trigger WebSocket communication
    start_time = time.time()
    call_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test websocket logging"}],
                "stream": True,
            },
        )

        # Consume the stream
        call_id = response.headers.get("x-litellm-call-id")
        chunks_received = []
        async for line in response.aiter_lines():
            if line.startswith("data: ") and not line.endswith("[DONE]"):
                chunk = json.loads(line[6:])
                if call_id is None:
                    call_id = chunk.get("id")
                chunks_received.append(chunk)

    assert call_id, "Streaming response missing call id"
    # Get logs and verify WebSocket OUT messages were logged
    logs = get_litellm_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )

    # Should see "WebSocket OUT" messages
    all_lines = logs.splitlines()
    websocket_out_logs = [line for line in all_lines if "WebSocket OUT" in line]

    assert len(websocket_out_logs) > 0, (
        f"Expected WebSocket OUT messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Should see at least a START message
    start_logs = [line for line in websocket_out_logs if "type=START" in line]
    assert len(start_logs) >= 1, (
        f"Expected at least one START message, found {len(start_logs)}. "
        f"WebSocket OUT logs:\n" + "\n".join(websocket_out_logs[:10])
    )

    # Should see CHUNK messages (backend chunks forwarded to control plane)
    chunk_logs = [line for line in websocket_out_logs if "type=CHUNK" in line]
    assert len(chunk_logs) >= 1, (
        f"Expected at least one CHUNK message, found {len(chunk_logs)}. "
        f"WebSocket OUT logs:\n" + "\n".join(websocket_out_logs[:10])
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_websocket_incoming_messages_logged():
    """Verify WebSocket IN messages (control plane → litellm) are logged."""
    # Make a streaming request to trigger WebSocket communication
    start_time = time.time()
    call_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test websocket logging"}],
                "stream": True,
            },
        )

        # Consume the stream
        call_id = response.headers.get("x-litellm-call-id")
        chunks_received = []
        async for line in response.aiter_lines():
            if line.startswith("data: ") and not line.endswith("[DONE]"):
                chunk = json.loads(line[6:])
                if call_id is None:
                    call_id = chunk.get("id")
                chunks_received.append(chunk)

    assert call_id, "Streaming response missing call id"
    # Get logs and verify WebSocket IN messages were logged
    logs = get_litellm_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )

    # Should see "WebSocket IN" messages
    all_lines = logs.splitlines()
    websocket_in_logs = [line for line in all_lines if "WebSocket IN" in line]
    assert len(websocket_in_logs) > 0, (
        f"Expected WebSocket IN messages in logs, but found none. Total log lines: {len(all_lines)}"
    )

    # Should see CHUNK messages coming back from control plane
    chunk_logs = [line for line in websocket_in_logs if "type=CHUNK" in line]
    assert len(chunk_logs) >= 1, (
        f"Expected at least one CHUNK message from control plane, found {len(chunk_logs)}. "
        f"WebSocket IN logs:\n" + "\n".join(websocket_in_logs[:10])
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_websocket_logs_include_stream_id():
    """Verify WebSocket logs include stream_id for correlation."""
    # Make a streaming request
    start_time = time.time()
    call_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"Authorization": "Bearer sk-luthien-dev-key"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "test stream id"}],
                "stream": True,
            },
        )

        # Consume the stream
        call_id = response.headers.get("x-litellm-call-id")
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            if call_id is None:
                chunk = json.loads(line[6:])
                call_id = chunk.get("id")

    assert call_id, "Streaming response missing call id"

    # Get logs and verify they include stream_id (UUID) for correlation
    logs = get_litellm_logs(
        since_time=start_time - 0.5,
        call_id=call_id,
    )

    # WebSocket logs should contain UUIDs as stream_id (format: [uuid])
    all_lines = logs.splitlines()
    websocket_logs = [line for line in all_lines if "WebSocket" in line]
    assert len(websocket_logs) > 0, "Should have at least one WebSocket log"

    # Verify that WebSocket logs contain stream IDs in [uuid] format
    uuid_pattern = re.compile(r"\[[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\]")
    logs_with_stream_ids = [line for line in websocket_logs if uuid_pattern.search(line)]
    assert len(logs_with_stream_ids) > 0, (
        "Expected WebSocket logs to contain stream_id in [uuid] format, but found none. "
        "First few WebSocket log lines:\n" + "\n".join(websocket_logs[:10])
    )
