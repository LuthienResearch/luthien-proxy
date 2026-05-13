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

The mock backend doesn't enforce this rule, so the e2e tests check the
invariant directly via two complementary regression tests:

1. `test_tool_use_is_last_block_when_judge_fails_under_pass` — inspects the
   SSE the proxy emits and asserts the LAST content block is a `tool_use`.
2. `test_next_turn_forwards_well_formed_assistant_message` — drives a
   two-turn conversation through `ClaudeCodeSimulator` and asserts the
   assistant message the proxy forwards upstream on turn 2 ends with a
   `tool_use`. This relies on the simulator being wire-faithful; prior to
   that change the simulator regrouped assistant content as
   `[merged_text, tool_use]`, silently masking the bug.

"""

from __future__ import annotations

import json

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import MockToolResponse, text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_anthropic.simulator import ClaudeCodeSimulator

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


@pytest.mark.asyncio
async def test_next_turn_forwards_well_formed_assistant_message(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    api_key,
    admin_api_key,
):
    """End-to-end check using the (now wire-faithful) ClaudeCodeSimulator.

    Drives Yash's two-turn flow:
      turn 1: user asks for an install; backend emits text preamble + tool_use;
              judge is unreachable, on_error='pass' kicks in.
      turn 2: simulator echoes the assistant message back verbatim along with a
              tool_result.

    Asserts the assistant message the proxy forwards upstream on turn 2 has its
    final content block as `tool_use`. Without the #708 fix, the warning text
    sits after the tool_use here and the real Anthropic API 400s. This test
    relies on the simulator preserving block order verbatim — before the
    simulator was made faithful it regrouped content as `[merged_text, tool_use]`
    and silently masked the bug.
    """
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(
        MockToolResponse(
            tool_name="Bash",
            tool_input={"command": "pip install scrapling"},
            text_preamble="I'll install Scrapling using pip.",
        )
    )
    mock_anthropic.enqueue(text_response("Scrapling installed successfully."))

    async with policy_context(
        _MULTI_SERIAL, _RAILWAY_LIKE_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        session = ClaudeCodeSimulator(gateway_url, api_key)
        turn1 = await session.send("Install scrapling")

        assert turn1.tool_calls, f"Expected a tool call on turn 1, got: {turn1!r}"
        assert "Safety judge unavailable" in turn1.text, (
            f"Expected judge-unavailable warning in turn 1 text, got: {turn1.text!r}"
        )

        # Drive turn 2; the proxy forwards the message history (including the
        # faithfully-preserved turn-1 assistant content) upstream.
        await session.continue_with_tool_result(turn1.tool_calls[0].id, "Successfully installed scrapling-0.2.0")

    upstream_requests = mock_anthropic.received_requests()
    assert len(upstream_requests) >= 2, f"Expected ≥2 upstream calls, got {len(upstream_requests)}"

    turn2_messages = upstream_requests[1]["messages"]
    assistant_msgs = [m for m in turn2_messages if m.get("role") == "assistant"]
    assert assistant_msgs, "Expected an assistant message in turn 2 upstream request"
    last_assistant = assistant_msgs[-1]
    content = last_assistant.get("content")
    assert isinstance(content, list) and content, f"Expected list-shaped assistant content, got: {content!r}"
    assert content[-1].get("type") == "tool_use", (
        f"Last block of forwarded assistant message must be tool_use; got block types "
        f"{[b.get('type') for b in content]}. Anthropic 400s otherwise (#708)."
    )
