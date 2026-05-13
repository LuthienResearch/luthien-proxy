"""Regression test for issue #708.

Yash hit `API Error: 400 due to tool use concurrency issues` running Claude
Code through the proxy with the Railway policy (MultiSerial[DebugLogging,
SimpleLLM]) when the judge was unreachable.

Root cause (confirmed against the real Anthropic API on 2026-05-13):
when SimpleLLMPolicy emitted a `tool_use` block under `on_error="pass"` after
a judge failure, it appended a `text` block (`"⚠️ Safety judge unavailable…"`)
*after* the `tool_use`. On the next turn, Claude Code echoed that assistant
content back verbatim along with a `tool_result`, and the Anthropic API
rejected it:

    {"type":"error","error":{"type":"invalid_request_error",
     "message":"messages.1: `tool_use` ids were found without `tool_result`
     blocks immediately after: toolu_… . Each `tool_use` block must have a
     corresponding `tool_result` block in the next message."}}

The mock backend doesn't enforce this rule, so the e2e test checks the
invariant directly: when SimpleLLMPolicy emits a streaming response with any
`tool_use` blocks, the LAST content block before `message_delta` must be a
`tool_use` (the warning, if present, must be injected earlier).

Why ClaudeCodeSimulator-based tests missed it: the simulator regroups
assistant content as `[all_text, all_tool_use]` when reconstructing history,
silently fixing the malformed ordering before turn 2. Real Claude Code
preserves block-index order verbatim, so the bug only surfaces against the
real API.
"""

from __future__ import annotations

import json

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import MockToolResponse
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_MULTI_SERIAL = "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy"
_DEBUG_LOGGING = "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy"
_SIMPLE_LLM = "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"

# Judge pointed at an unreachable port to force on_error="pass" path.
_RAILWAY_LIKE_CONFIG = {
    "policies": [
        {"class": _DEBUG_LOGGING, "config": {}},
        {
            "class": _SIMPLE_LLM,
            "config": {
                "instructions": "Review responses per safety rules.",
                "model": "claude-haiku-4-5",
                "api_base": "http://127.0.0.1:19999",
                "auth_provider": "user_credentials",
                "on_error": "pass",
            },
        },
    ]
}


def _parse_sse(raw_text: str) -> list[dict]:
    events: list[dict] = []
    for line in raw_text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def _block_types_in_order(events: list[dict]) -> list[str]:
    """Return content block types in the order they appear in the stream."""
    by_index: dict[int, str] = {}
    order: list[int] = []
    for ev in events:
        if ev.get("type") == "content_block_start":
            idx = ev["index"]
            kind = ev.get("content_block", {}).get("type")
            by_index[idx] = kind
            order.append(idx)
    return [by_index[i] for i in order]


@pytest.mark.asyncio
async def test_tool_use_is_last_block_when_judge_fails_under_pass(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """Regression for #708.

    When SimpleLLMPolicy emits a response containing a tool_use with
    on_error='pass' after a judge failure, the LAST content block before
    message_delta must be a tool_use. The judge-unavailable warning (if any)
    must appear before the tool_use, not after — otherwise the real Anthropic
    API returns 400 on the next turn (verified 2026-05-13).
    """
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(
        MockToolResponse(
            tool_name="Bash",
            tool_input={"command": "pip install scrapling"},
            text_preamble="I'll install Scrapling using pip.",
        )
    )

    async with policy_context(
        _MULTI_SERIAL, _RAILWAY_LIKE_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        payload = {
            "model": "claude-haiku-4-5",
            "max_tokens": 1024,
            "stream": True,
            "system": [{"type": "text", "text": "You are Claude Code."}],
            "tools": [
                {
                    "name": "Bash",
                    "description": "Run a bash command",
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
            "messages": [{"role": "user", "content": "Install scrapling"}],
        }
        headers = {"Authorization": f"Bearer {api_key}"}

        async with httpx.AsyncClient(timeout=20.0) as client:
            async with client.stream("POST", f"{gateway_url}/v1/messages", json=payload, headers=headers) as r:
                r.raise_for_status()
                raw = await r.aread()

    events = _parse_sse(raw.decode())
    block_types = _block_types_in_order(events)

    # Sanity: a tool_use must be present and the warning must be emitted
    assert "tool_use" in block_types, f"Expected a tool_use block, got: {block_types}"
    warning_text = "Safety judge unavailable"
    assert any(
        ev.get("type") == "content_block_delta" and warning_text in (ev.get("delta", {}).get("text") or "")
        for ev in events
    ), "Expected judge-unavailable warning to be present in the stream"

    # The invariant: tool_use must be the last content block before message_delta.
    # If a text(warning) follows the tool_use, the real Anthropic API rejects
    # the next turn with `tool_use ids were found without tool_result blocks
    # immediately after`.
    assert block_types[-1] == "tool_use", (
        f"Expected last content block to be tool_use; got order: {block_types}. "
        "A text block after tool_use causes Anthropic 400 on the next turn (#708)."
    )

    # stop_reason should still be "tool_use" since a tool_use is present
    stop_reasons = [ev.get("delta", {}).get("stop_reason") for ev in events if ev.get("type") == "message_delta"]
    assert stop_reasons and stop_reasons[-1] == "tool_use", f"Expected stop_reason='tool_use', got: {stop_reasons}"
