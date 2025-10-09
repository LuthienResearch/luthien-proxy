"""End-to-end tests validating the LLM judge tool-call policy.

The judge policy evaluates tool calls that the LLM returns and blocks harmful ones
based on the judge model's assessment. The dummy provider returns actual tool calls
for the "harmful_drop" scenario, allowing us to test blocking behavior.
"""

from __future__ import annotations

import httpx
import pytest
from tests.e2e_tests.helpers import E2ESettings  # noqa: E402
from tests.e2e_tests.helpers.policy_assertions import build_policy_payload  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def policy_config_path() -> str:
    return "config/policies/tool_call_judge.yaml"


@pytest.mark.asyncio
async def test_judge_policy_blocks_harmful_tool_call_non_streaming(
    use_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Verify judge policy blocks harmful tool calls in non-streaming mode."""
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
    content = message.get("content", "")

    # Should be blocked with BLOCKED message
    assert "BLOCKED" in content
    assert "execute_sql" in content
    assert not message.get("tool_calls"), "Tool calls should be blocked"

    assert response.headers.get("x-litellm-call-id") or response.headers.get("litellm-call-id"), (
        "Expected litellm call id in headers"
    )


@pytest.mark.asyncio
async def test_judge_policy_blocks_harmful_tool_call_streaming(
    use_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Verify judge policy blocks harmful tool calls in streaming mode."""
    payload = build_policy_payload(e2e_settings, stream=True)
    headers = {
        "Authorization": f"Bearer {e2e_settings.master_key}",
        "Content-Type": "application/json",
    }

    chunks = []
    call_id = None
    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            assert response.status_code == 200
            call_id = response.headers.get("x-litellm-call-id") or response.headers.get("litellm-call-id")

            async for line in response.aiter_lines():
                if line.startswith("data: ") and not line.endswith("[DONE]"):
                    import json

                    chunk = json.loads(line[6:])
                    chunks.append(chunk)

    assert chunks, "Expected at least one streamed chunk"

    # Find blocked chunk
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
    assert "execute_sql" in delta.get("content", "")

    assert call_id, "Expected streaming call id in headers or body"
