"""Regression tests for the known-bad API request patterns from prior COEs.

Each pattern below produced a production 400 error in the old LiteLLM-era
architecture and was point-fixed with a request sanitizer (PRs #201, #167,
#178, #151). None of those sanitizers survived the refactor to the direct
Anthropic SDK pipeline — deliberately: the current architecture is
transparency-first. The proxy forwards requests verbatim (unknown fields via
extra_body, client anthropic-beta headers forwarded) so behavior through the
proxy matches a direct connection as the API evolves.

These tests pin that contract per pattern:

1. The known-bad fixture passes through ``AnthropicClient._prepare_request_kwargs``
   unmodified (no silent sanitization creeps back in, no proxy-side crash).
2. When the upstream API rejects the pattern, the pipeline relays the API's own
   400 (``invalid_request_error``) to the client — never a proxy-side 500.

Upstream behavior was verified against the live Anthropic API on 2026-07-06
(model claude-haiku-4-5); the per-pattern result is recorded in each test
class docstring. Two of the historical patterns are no longer rejected
upstream, which makes the old sanitizers actively harmful today:

- Whitespace-only text blocks (half of PR #201's fix) are now accepted.
- ``context_management`` (stripped by PR #151) is now a real API feature that
  Claude Code sends on every request; stripping it would silently disable
  context editing.

Pattern 6 (parallel tool_use ordering, PR #356) is a proxy-side streaming
protocol bug, not a request sanitization gap; its fix is in current main and
covered by tests/luthien_proxy/unit_tests/test_anthropic_stream_validator.py
and tests/luthien_proxy/e2e_tests/test_mock_simple_llm_parallel_tools.py. A
tripwire test here re-asserts the validator flags PR #356's exact failure mode.

Audit source: Trello cards mWjeUBG1 and qGTbhaTa (COE audit 2026-03-25).
"""

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic import APIStatusError as AnthropicStatusError
from httpx import Request as HttpxRequest
from httpx import Response as HttpxResponse
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import AnthropicRequest
from luthien_proxy.pipeline.anthropic_processor import (
    _handle_anthropic_error,
    process_anthropic_request,
)
from luthien_proxy.pipeline.stream_protocol_validator import validate_anthropic_event_ordering
from luthien_proxy.policies.noop_policy import NoOpPolicy

# ---------------------------------------------------------------------------
# Known-bad request fixtures (verbatim from the original bug reports)
# ---------------------------------------------------------------------------

# PR #201: Claude Code accumulated empty text blocks from MCP tool result
# assembly. Live API 2026-07-06: still rejected —
# "messages: text content blocks must be non-empty".
EMPTY_TEXT_BLOCK_REQUEST: AnthropicRequest = {
    "model": DEFAULT_TEST_MODEL,
    "max_tokens": 64,
    "messages": [
        {"role": "user", "content": "say hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": ""},
                {"type": "text", "text": "Hello!"},
            ],
        },
        {"role": "user", "content": "say ok"},
    ],
}

# PR #201 (extension): whitespace-only text blocks were also rejected in 2026-02.
# Live API 2026-07-06: now ACCEPTED (returns a normal message). Kept as a fixture
# to document the constraint change; forwarding verbatim is required behavior.
WHITESPACE_TEXT_BLOCK_REQUEST: AnthropicRequest = {
    "model": DEFAULT_TEST_MODEL,
    "max_tokens": 64,
    "messages": [
        {"role": "user", "content": "say hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": " "},
                {"type": "text", "text": "Hello!"},
            ],
        },
        {"role": "user", "content": "say ok"},
    ],
}

# PR #167: /compact removed the assistant tool_use but kept the tool_result.
# Live API 2026-07-06: still rejected — "unexpected `tool_use_id` found in
# `tool_result` blocks ... must have a corresponding `tool_use` block".
ORPHANED_TOOL_RESULT_REQUEST: AnthropicRequest = {
    "model": DEFAULT_TEST_MODEL,
    "max_tokens": 64,
    "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_orphan123",
                    "content": "result text",
                }
            ],
        },
    ],
}

# PR #178: Claude Code sent cache_control {"type": "ephemeral", "scope": "turn"}
# on tool definitions. Live API 2026-07-06: still rejected without a matching
# beta header — "cache_control.ephemeral.scope: Extra inputs are not permitted".
# Note: current Claude Code no longer sends `scope` on tools (verified against
# a recorded 2026-04 dogfooding request), and the pipeline forwards the client's
# anthropic-beta header (PR #269) so beta-gated cache features work when the
# client opts in.
CACHE_CONTROL_EXTRA_FIELD_REQUEST: AnthropicRequest = {
    "model": DEFAULT_TEST_MODEL,
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [
        {
            "name": "get_weather",
            "description": "w",
            "input_schema": {"type": "object", "properties": {}},
            "cache_control": {"type": "ephemeral", "scope": "turn"},
        }
    ],
}

# PR #151 stripped `context_management` because the 2026-01 API rejected it.
# It is now a real API feature (context editing) that Claude Code sends on
# every request, in the `edits` shape below (verbatim from a recorded 2026-04
# dogfooding request). Live API 2026-07-06: ACCEPTED. Stripping it today would
# silently disable context editing — forwarding is the required behavior.
CONTEXT_MANAGEMENT_REQUEST: AnthropicRequest = {
    "model": DEFAULT_TEST_MODEL,
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hi"}],
    "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
}  # type: ignore[typeddict-unknown-key]

# Known-unfixed pattern from the old TODO (never got its own PR). Live API
# 2026-07-06: still rejected — "tools: Tool names must be unique."
DUPLICATE_TOOLS_REQUEST: AnthropicRequest = {
    "model": DEFAULT_TEST_MODEL,
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [
        {"name": "get_weather", "description": "w1", "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_weather", "description": "w2", "input_schema": {"type": "object", "properties": {}}},
    ],
}


def _make_client() -> AnthropicClient:
    return AnthropicClient(api_key="test-key")


def _make_400(message: str) -> AnthropicStatusError:
    """Build an AnthropicStatusError matching a live invalid_request_error."""
    body = {"error": {"type": "invalid_request_error", "message": message}}
    response = HttpxResponse(
        status_code=400,
        request=HttpxRequest("POST", "https://api.anthropic.com/v1/messages"),
        json=body,
    )
    return AnthropicStatusError(message=message, response=response, body=body)


# ---------------------------------------------------------------------------
# Contract 1: known-bad patterns pass through _prepare_request_kwargs verbatim
# ---------------------------------------------------------------------------


class TestKnownBadPatternsForwardedVerbatim:
    """The client wrapper must not crash on, nor silently rewrite, these payloads.

    If a future change reintroduces sanitization on purpose, update these
    tests deliberately — they exist so that change is a decision, not drift.
    """

    @pytest.mark.parametrize(
        "request_fixture",
        [
            pytest.param(EMPTY_TEXT_BLOCK_REQUEST, id="empty-text-block"),
            pytest.param(WHITESPACE_TEXT_BLOCK_REQUEST, id="whitespace-text-block"),
            pytest.param(ORPHANED_TOOL_RESULT_REQUEST, id="orphaned-tool-result"),
            pytest.param(CACHE_CONTROL_EXTRA_FIELD_REQUEST, id="cache-control-extra-field"),
            pytest.param(DUPLICATE_TOOLS_REQUEST, id="duplicate-tools"),
        ],
    )
    def test_messages_and_tools_forwarded_unmodified(self, request_fixture: AnthropicRequest):
        original = copy.deepcopy(request_fixture)
        kwargs = _make_client()._prepare_request_kwargs(request_fixture)

        assert kwargs["messages"] == original["messages"]
        if "tools" in original:
            assert kwargs["tools"] == original["tools"]  # type: ignore[typeddict-item]
        # The input dict itself must not be mutated either.
        assert request_fixture == original

    def test_empty_text_block_survives_forwarding(self):
        """PR #201 regression tripwire: the empty block is still in the payload."""
        kwargs = _make_client()._prepare_request_kwargs(EMPTY_TEXT_BLOCK_REQUEST)
        assistant_blocks = kwargs["messages"][1]["content"]
        assert {"type": "text", "text": ""} in assistant_blocks

    def test_orphaned_tool_result_survives_forwarding(self):
        """PR #167 regression tripwire: the orphaned tool_result is not pruned."""
        kwargs = _make_client()._prepare_request_kwargs(ORPHANED_TOOL_RESULT_REQUEST)
        tool_result = kwargs["messages"][2]["content"][0]
        assert tool_result["tool_use_id"] == "toolu_orphan123"

    def test_cache_control_scope_survives_forwarding(self):
        """PR #178 regression tripwire: extra cache_control fields are not stripped."""
        kwargs = _make_client()._prepare_request_kwargs(CACHE_CONTROL_EXTRA_FIELD_REQUEST)
        assert kwargs["tools"][0]["cache_control"] == {"type": "ephemeral", "scope": "turn"}

    def test_context_management_forwarded_via_extra_body(self):
        """PR #151 inversion: context_management must be FORWARDED, not stripped.

        It is not an SDK named parameter, so it must travel via extra_body to
        reach the API. Claude Code sends it on every request; dropping it would
        silently disable context editing.
        """
        kwargs = _make_client()._prepare_request_kwargs(CONTEXT_MANAGEMENT_REQUEST)
        assert "context_management" not in kwargs
        assert kwargs["extra_body"]["context_management"] == {
            "edits": [{"type": "clear_thinking_20251015", "keep": "all"}]
        }


# ---------------------------------------------------------------------------
# Contract 2: upstream 400 rejections are relayed cleanly (never a proxy 500)
# ---------------------------------------------------------------------------

_LIVE_400_MESSAGES = {
    "empty-text-block": "messages: text content blocks must be non-empty",
    "orphaned-tool-result": (
        "messages.2.content.0: unexpected `tool_use_id` found in `tool_result` blocks: "
        "toolu_orphan123. Each `tool_result` block must have a corresponding `tool_use` "
        "block in the previous message."
    ),
    "cache-control-extra-field": "tools.0.custom.cache_control.ephemeral.scope: Extra inputs are not permitted",
    "duplicate-tools": "tools: Tool names must be unique.",
}


class TestUpstream400RelayedCleanly:
    """When the API rejects a known-bad pattern, the client sees the API's own
    400 invalid_request_error — same as a direct connection — not a proxy 500.
    """

    @pytest.mark.parametrize("pattern_id", sorted(_LIVE_400_MESSAGES))
    def test_error_classified_as_invalid_request_error(self, pattern_id: str):
        message = _LIVE_400_MESSAGES[pattern_id]
        with pytest.raises(BackendAPIError) as exc_info:
            _handle_anthropic_error(_make_400(message), "test-call")

        assert exc_info.value.status_code == 400
        assert exc_info.value.error_type == "invalid_request_error"
        assert message in exc_info.value.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("request_fixture", "pattern_id"),
        [
            pytest.param(EMPTY_TEXT_BLOCK_REQUEST, "empty-text-block", id="empty-text-block"),
            pytest.param(ORPHANED_TOOL_RESULT_REQUEST, "orphaned-tool-result", id="orphaned-tool-result"),
            pytest.param(
                CACHE_CONTROL_EXTRA_FIELD_REQUEST, "cache-control-extra-field", id="cache-control-extra-field"
            ),
            pytest.param(DUPLICATE_TOOLS_REQUEST, "duplicate-tools", id="duplicate-tools"),
        ],
    )
    async def test_pipeline_relays_400_end_to_end(self, request_fixture: AnthropicRequest, pattern_id: str):
        """Full pipeline: known-bad body in, upstream 400 out, faithfully relayed.

        The mocked AnthropicClient raises the exact error the live API returned
        for this fixture on 2026-07-06. The pipeline must surface it as a
        BackendAPIError(400) — the main.py handler formats that as an
        Anthropic-shaped error response for the client.
        """
        body = copy.deepcopy(request_fixture)
        body["stream"] = False

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.method = "POST"
        mock_request.url = MagicMock()
        mock_request.url.path = "/v1/messages"
        mock_request.json = AsyncMock(return_value=body)

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(side_effect=_make_400(_LIVE_400_MESSAGES[pattern_id]))

        with pytest.raises(BackendAPIError) as exc_info:
            await process_anthropic_request(
                request=mock_request,
                policy=NoOpPolicy(),
                anthropic_client=mock_client,
                emitter=MagicMock(),
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.error_type == "invalid_request_error"

        # Transparency check: the known-bad payload reached the backend intact.
        forwarded = mock_client.complete.call_args.args[0]
        assert forwarded["messages"] == body["messages"]
        if "tools" in body:
            assert forwarded["tools"] == body["tools"]  # type: ignore[typeddict-item]


# ---------------------------------------------------------------------------
# Pattern 6 (PR #356): parallel tool_use / streaming event ordering
# ---------------------------------------------------------------------------


class TestParallelToolUseOrderingTripwire:
    """PR #356: content blocks injected after message_delta bricked sessions.

    The architectural fix is validate_anthropic_event_ordering, wired into the
    streaming pipeline (advisory log + streaming.protocol_violation event).
    Full coverage lives in test_anthropic_stream_validator.py and the mock e2e
    test test_mock_simple_llm_parallel_tools.py; this tripwire re-asserts the
    validator catches PR #356's exact failure mode.
    """

    def test_content_block_after_message_delta_is_flagged(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_1"}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            # PR #356's bug: warning text block injected after message_delta.
            {"type": "content_block_start", "index": 2, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "warning"}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_stop"},
        ]

        result = validate_anthropic_event_ordering(events)

        assert not result.valid
        assert any("message_delta" in v.message or "message_delta" in v.rule for v in result.violations)

    def test_correctly_ordered_parallel_tool_use_is_valid(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_1"}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use"}},
            {"type": "content_block_stop", "index": 1},
            # Post-fix behavior: warning injected BEFORE message_delta.
            {"type": "content_block_start", "index": 2, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "warning"}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        ]

        result = validate_anthropic_event_ordering(events)

        result.assert_valid()
