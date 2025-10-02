"""End-to-end tests validating the SQL protection policy."""

from __future__ import annotations

import httpx
import pytest
from tests.e2e_tests.helpers import E2ESettings  # noqa: E402
from tests.e2e_tests.helpers.policy_assertions import (  # noqa: E402
    build_policy_payload,
    fetch_block_trace,
    stream_policy_block,
)

pytestmark = pytest.mark.e2e

DEBUG_TYPE = "protection:sql-block"


@pytest.fixture(scope="module")
def policy_config_path() -> str:
    return "config/policies/sql_protection.yaml"


@pytest.mark.asyncio
async def test_sql_policy_blocks_non_streaming_via_callback(
    use_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    payload = build_policy_payload(e2e_settings, stream=False)
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
    assert response.status_code == 200, response.text

    body = response.json()
    message = body["choices"][0]["message"]
    content = message.get("content")
    assert isinstance(content, str) and "BLOCKED" in content
    assert not message.get("tool_calls")

    call_id = response.headers.get("x-litellm-call-id") or body.get("id")
    if not call_id:
        call_id = response.headers.get("litellm-call-id")
    assert call_id, "Expected litellm call id in headers or body"

    await fetch_block_trace(e2e_settings, call_id, DEBUG_TYPE)


@pytest.mark.asyncio
async def test_sql_policy_blocks_streaming_via_callback(
    use_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    headers = {
        "Authorization": f"Bearer {e2e_settings.master_key}",
        "Content-Type": "application/json",
    }

    call_id, chunks = await stream_policy_block(e2e_settings, headers)

    assert chunks, "Expected at least one streamed chunk"
    blocked_chunks = [
        chunk
        for chunk in chunks
        if "BLOCKED" in ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content", "")
    ]
    assert blocked_chunks, "Expected a blocked chunk in streaming response"
    final_chunk = blocked_chunks[-1]
    choice = (final_chunk.get("choices") or [{}])[0]
    assert choice.get("finish_reason") == "stop"
    delta = choice.get("delta") or {}
    assert "BLOCKED" in delta.get("content", "")
    assert not delta.get("tool_calls")

    await fetch_block_trace(e2e_settings, call_id, DEBUG_TYPE)
