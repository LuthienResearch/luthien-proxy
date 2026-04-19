"""Unit tests for MultiBackendPolicy.

Covers:
- Protocol compliance (inherits required base classes).
- Config validation (empty models rejected).
- Streaming: labeled sections in arrival order, monotonic indices,
  single message_start/stop framing.
- Non-streaming: all models' responses aggregated as labeled text.
- Tool-use rendered as labeled text, not forwarded verbatim.
- Per-model failure surfaces as an error block, not a request failure.
- Missing ``user_credential`` raises CredentialError.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    Message,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawMessageStartEvent,
    Usage,
)
from anthropic.types.thinking_delta import ThinkingDelta
from pydantic import ValidationError
from tests.luthien_proxy.unit_tests.policies.anthropic_event_builders import (
    block_stop,
    message_delta,
    text_delta,
    text_start,
    tool_delta,
    tool_start,
)

from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policies.multi_backend_policy import (
    MultiBackendConfig,
    MultiBackendPolicy,
    _request_for_model,
)
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicHookPolicy,
    BasePolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

# =============================================================================
# Helpers
# =============================================================================


def _credential() -> Credential:
    return Credential(value="sk-fake", credential_type=CredentialType.API_KEY)


def _context_with_credential() -> PolicyContext:
    return PolicyContext.for_testing(transaction_id="test-txn", user_credential=_credential())


def _message_start(model: str = "claude-test") -> RawMessageStartEvent:
    return RawMessageStartEvent.model_construct(
        type="message_start",
        message=Message.model_construct(
            id="msg_fake",
            type="message",
            role="assistant",
            model=model,
            content=[],
            stop_reason=None,
            stop_sequence=None,
            usage=Usage(input_tokens=5, output_tokens=0),
        ),
    )


class _FakeStreamingClient:
    """AnthropicClient stand-in for streaming tests — yields prebuilt events.

    If ``release_gate`` is provided, the client waits on that event before
    yielding any values, letting tests control arrival ordering.
    """

    def __init__(
        self,
        events: list[MessageStreamEvent],
        raise_on_stream: Exception | None = None,
        release_gate: "asyncio.Event | None" = None,
    ) -> None:
        self._events = events
        self._raise = raise_on_stream
        self._gate = release_gate
        self.closed = False

    def stream(
        self,
        request: AnthropicRequest,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[MessageStreamEvent]:
        events = self._events
        should_raise = self._raise
        gate = self._gate

        async def _gen() -> AsyncIterator[MessageStreamEvent]:
            if gate is not None:
                await gate.wait()
            for ev in events:
                yield ev
            if should_raise is not None:
                raise should_raise

        return _gen()

    async def close(self) -> None:
        self.closed = True


class _FakeCompleteClient:
    """AnthropicClient stand-in for non-streaming tests."""

    def __init__(self, response: AnthropicResponse | Exception) -> None:
        self._response = response
        self.closed = False

    async def complete(
        self,
        request: AnthropicRequest,
        extra_headers: dict[str, str] | None = None,
    ) -> AnthropicResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def close(self) -> None:
        self.closed = True


def _install_streaming_clients(
    policy: MultiBackendPolicy,
    clients_by_model: dict[str, _FakeStreamingClient],
) -> None:
    """Patch ``_client_for_credential`` to return a client based on the next-requested model.

    The policy calls ``_client_for_credential(credential)`` once per extra model
    (in config order), so we vend them in that order.
    """
    pending = [clients_by_model[name] for name in list(clients_by_model.keys())]
    pending_iter = iter(pending)

    def _factory(credential: Credential) -> _FakeStreamingClient:  # type: ignore[override]
        return next(pending_iter)

    policy._client_for_credential = _factory  # type: ignore[method-assign]


def _install_complete_clients(
    policy: MultiBackendPolicy,
    clients_by_model: dict[str, _FakeCompleteClient],
) -> None:
    pending = [clients_by_model[name] for name in list(clients_by_model.keys())]
    pending_iter = iter(pending)

    def _factory(credential: Credential) -> _FakeCompleteClient:  # type: ignore[override]
        return next(pending_iter)

    policy._client_for_credential = _factory  # type: ignore[method-assign]


async def _run_streaming(
    policy: MultiBackendPolicy,
    ctx: PolicyContext,
    primary_events: list[MessageStreamEvent],
    request: AnthropicRequest,
) -> list[MessageStreamEvent]:
    """Drive the streaming hook lifecycle and return the concatenated emissions."""
    modified = await policy.on_anthropic_request(request, ctx)
    assert modified["model"] == policy.config.models[0]

    emitted: list[MessageStreamEvent] = []
    for ev in primary_events:
        emitted.extend(await policy.on_anthropic_stream_event(ev, ctx))
    emitted.extend(cast("list[MessageStreamEvent]", await policy.on_anthropic_stream_complete(ctx)))
    return emitted


def _event_types(events: list[MessageStreamEvent]) -> list[str]:
    return [getattr(e, "type", "") for e in events]


def _text_of_block(events: list[MessageStreamEvent], index: int) -> str:
    """Concatenate all text_delta text for a given content_block index."""
    parts: list[str] = []
    for ev in events:
        if getattr(ev, "type", None) != "content_block_delta":
            continue
        if getattr(ev, "index", None) != index:
            continue
        delta = getattr(ev, "delta", None)
        if getattr(delta, "type", None) != "text_delta":
            continue
        parts.append(getattr(delta, "text", ""))
    return "".join(parts)


# =============================================================================
# Protocol compliance
# =============================================================================


class TestProtocol:
    def test_inherits_base_policy(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a"]))
        assert isinstance(policy, BasePolicy)

    def test_inherits_hook_policy(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a"]))
        assert isinstance(policy, AnthropicHookPolicy)

    def test_implements_execution_interface(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a"]))
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_short_policy_name(self):
        assert MultiBackendPolicy(MultiBackendConfig(models=["a"])).short_policy_name == "MultiBackend"

    def test_freeze_configured_state_ok(self):
        # No mutable instance attrs (config is a Pydantic model; _models is a tuple).
        MultiBackendPolicy(MultiBackendConfig(models=["a", "b"])).freeze_configured_state()


# =============================================================================
# Config validation
# =============================================================================


class TestPerModelRequestShape:
    """_request_for_model encodes per-model capability profiles."""

    def _base_request(self) -> AnthropicRequest:
        return {
            "model": "client-requested",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "thinking": {"type": "adaptive"},
            "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
            "effort": "medium",
            "output_config": {"whatever": True},
        }

    def test_haiku_drops_thinking_context_management_effort_output_config(self):
        out = _request_for_model(self._base_request(), "claude-haiku-4-5")
        assert out["model"] == "claude-haiku-4-5"
        assert "thinking" not in out
        assert "context_management" not in out
        assert "effort" not in out
        assert "output_config" not in out
        # Core fields survive (the first user message gets the multi-backend
        # note prepended; the original text is retained).
        assert "hi" in out["messages"][0]["content"]
        assert out["max_tokens"] == 100

    def test_opus_keeps_thinking_and_context_management(self):
        out = _request_for_model(self._base_request(), "claude-opus-4-7")
        assert out["model"] == "claude-opus-4-7"
        assert out.get("thinking") == {"type": "adaptive"}
        assert "context_management" in out

    def test_sonnet_keeps_thinking_and_context_management(self):
        out = _request_for_model(self._base_request(), "claude-sonnet-4-6")
        assert out.get("thinking") == {"type": "adaptive"}
        assert "context_management" in out

    def test_deep_copies_not_aliased_with_input(self):
        src = self._base_request()
        out = _request_for_model(src, "claude-opus-4-7")
        out["messages"].append({"role": "user", "content": "extra"})
        assert len(src["messages"]) == 1

    def test_injects_multi_backend_note_into_first_user_message(self):
        out = _request_for_model(self._base_request(), "claude-opus-4-7")
        first_user = out["messages"][0]
        content = first_user["content"]
        # First block (or prepended prefix) carries the tagged note.
        if isinstance(content, list):
            assert content[0]["type"] == "text"
            assert "<multi-backend-context>" in content[0]["text"]
            assert "respond only on behalf of yourself" in content[0]["text"].lower()
        else:
            assert "<multi-backend-context>" in content

    def test_note_is_idempotent_across_turns(self):
        """Don't re-inject if the note is already present in history."""
        req = self._base_request()
        once = _request_for_model(req, "claude-opus-4-7")
        twice = _request_for_model(once, "claude-sonnet-4-6")
        flat = ""
        for block in twice["messages"][0]["content"]:
            flat += block["text"] if isinstance(block, dict) else str(block)
        assert flat.count("<multi-backend-context>") == 1


class TestConfig:
    def test_requires_at_least_one_model(self):
        with pytest.raises(ValidationError):
            MultiBackendConfig(models=[])

    def test_accepts_dict_config(self):
        policy = MultiBackendPolicy({"models": ["a", "b"]})
        assert policy.config.models == ["a", "b"]

    def test_get_config_roundtrip(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a", "b"]))
        assert policy.get_config() == {"models": ["a", "b"], "stagger_seconds": 0.0}


# =============================================================================
# Missing credential
# =============================================================================


class TestMissingCredential:
    @pytest.mark.asyncio
    async def test_raises_credential_error_when_user_credential_absent(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a", "b"]))
        ctx = PolicyContext.for_testing(transaction_id="t", user_credential=None)
        request: AnthropicRequest = {"model": "ignored", "messages": [], "max_tokens": 10, "stream": True}
        with pytest.raises(CredentialError):
            await policy.on_anthropic_request(request, ctx)


# =============================================================================
# Streaming
# =============================================================================


class TestStreaming:
    @pytest.mark.asyncio
    async def test_streaming_emits_one_labeled_block_per_model(self):
        """Both models produce labeled sections with valid framing and monotonic indices."""
        policy = MultiBackendPolicy(MultiBackendConfig(models=["primary-model", "extra-model"]))
        ctx = _context_with_credential()

        extra_events: list[MessageStreamEvent] = [
            _message_start("extra-model"),
            text_start(index=0),
            text_delta("extra-content-A", index=0),
            text_delta("-B", index=0),
            block_stop(index=0),
            message_delta(stop_reason="end_turn"),
        ]
        _install_streaming_clients(
            policy,
            {"extra-model": _FakeStreamingClient(extra_events)},
        )

        primary_events: list[MessageStreamEvent] = [
            _message_start("primary-model"),
            text_start(index=0),
            text_delta("primary-content-X", index=0),
            text_delta("-Y", index=0),
            block_stop(index=0),
            message_delta(stop_reason="end_turn"),
        ]
        request: AnthropicRequest = {
            "model": "client-requested-model",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "stream": True,
        }

        emitted = await _run_streaming(policy, ctx, primary_events, request)
        types = _event_types(emitted)

        # Framing invariants.
        assert types[0] == "message_start"
        assert types.count("message_start") == 1
        assert types[-1] == "message_stop"
        assert types.count("message_stop") == 1
        assert types[-2] == "message_delta"

        # Content blocks are paired and indices are strictly monotonic.
        block_starts = [i for i, t in enumerate(types) if t == "content_block_start"]
        block_stops = [i for i, t in enumerate(types) if t == "content_block_stop"]
        assert len(block_starts) == len(block_stops)
        indices = [getattr(emitted[i], "index", -1) for i in block_starts]
        assert indices == sorted(indices)
        assert len(indices) == len(set(indices))

        # Exactly two models, each preceded by a header block: 4 blocks total (header + content x2).
        assert len(block_starts) == 4

        # Both labels appear, and each precedes its own content.
        label_texts = [_text_of_block(emitted, idx) for idx in indices if _text_of_block(emitted, idx).startswith("# ")]
        assert set(label_texts) == {"# primary-model\n\n", "# extra-model\n\n"}

        # Content of each model is present.
        all_text = "".join(_text_of_block(emitted, idx) for idx in indices)
        assert "primary-content-X-Y" in all_text
        assert "extra-content-A-B" in all_text

    @pytest.mark.asyncio
    async def test_arrival_order_determines_section_order(self):
        """Gating the extra model ensures primary arrives first, hence labeled first."""
        policy = MultiBackendPolicy(MultiBackendConfig(models=["primary-model", "extra-model"]))
        ctx = _context_with_credential()

        gate = asyncio.Event()
        extra_events: list[MessageStreamEvent] = [
            _message_start("extra-model"),
            text_start(index=0),
            text_delta("extra-text", index=0),
            block_stop(index=0),
            message_delta(),
        ]
        _install_streaming_clients(
            policy,
            {"extra-model": _FakeStreamingClient(extra_events, release_gate=gate)},
        )

        primary_events: list[MessageStreamEvent] = [
            _message_start("primary-model"),
            text_start(index=0),
            text_delta("primary-text", index=0),
            block_stop(index=0),
            message_delta(),
        ]
        request: AnthropicRequest = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": True,
        }

        modified = await policy.on_anthropic_request(request, ctx)
        assert modified["model"] == "primary-model"

        # Feed primary while extra is gated — primary announces first.
        for ev in primary_events:
            await policy.on_anthropic_stream_event(ev, ctx)
        # Now let extra proceed; wait for completion via stream_complete.
        gate.set()
        tail = await policy.on_anthropic_stream_complete(ctx)

        # Collect everything by re-running through the hook's return accumulator.
        # (We already consumed the output from on_anthropic_stream_event; the
        # remaining events come from on_anthropic_stream_complete.)
        # For the order check we only need to confirm the first label seen overall
        # was "primary-model". The first header appears in output_queue in the
        # order the coordinator emitted it. Since the coordinator processes
        # arrivals in arrival order, and primary announced first, primary's
        # label is the first header emitted.
        label_texts_tail = [
            getattr(getattr(ev, "delta", None), "text", "")
            for ev in tail
            if getattr(ev, "type", None) == "content_block_delta"
            and getattr(getattr(ev, "delta", None), "text", "").startswith("# ")
        ]
        # Extra's label is in the tail; primary's was consumed earlier.
        assert label_texts_tail == ["# extra-model\n\n"]

    @pytest.mark.asyncio
    async def test_extra_model_failure_becomes_error_block(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["primary", "extra"]))
        ctx = _context_with_credential()

        _install_streaming_clients(
            policy,
            {"extra": _FakeStreamingClient([], raise_on_stream=RuntimeError("backend blew up"))},
        )

        primary_events: list[MessageStreamEvent] = [
            _message_start("primary"),
            text_start(index=0),
            text_delta("primary-text", index=0),
            block_stop(index=0),
            message_delta(),
        ]
        request: AnthropicRequest = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": True,
        }

        emitted = await _run_streaming(policy, ctx, primary_events, request)
        all_text = "".join(
            _text_of_block(emitted, getattr(e, "index", -1))
            for e in emitted
            if getattr(e, "type", None) == "content_block_start"
        )
        assert "# primary\n\n" in all_text
        assert "# extra\n\n" in all_text
        assert "**Error:**" in all_text
        assert "backend blew up" in all_text

    @pytest.mark.asyncio
    async def test_thinking_blocks_are_skipped_not_emitted_as_empty_text(self):
        """Thinking blocks from the primary stream must not round-trip as empty text.

        Empty text content blocks trigger Anthropic's "text content blocks must
        be non-empty" error on the next turn's history replay. Regression test
        for exactly that failure seen during live Claude Code testing.
        """
        policy = MultiBackendPolicy(MultiBackendConfig(models=["primary", "extra"]))
        ctx = _context_with_credential()

        _install_streaming_clients(
            policy,
            {"extra": _FakeStreamingClient([_message_start("extra"), message_delta()])},
        )

        thinking_start = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block={"type": "thinking", "thinking": ""},
        )
        thinking_delta_ev = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=ThinkingDelta.model_construct(type="thinking_delta", thinking="reasoning..."),
        )
        primary_events: list[MessageStreamEvent] = [
            _message_start("primary"),
            thinking_start,
            thinking_delta_ev,
            block_stop(index=0),
            text_start(index=1),
            text_delta("real-reply", index=1),
            block_stop(index=1),
            message_delta(),
        ]
        request: AnthropicRequest = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": True,
        }

        emitted = await _run_streaming(policy, ctx, primary_events, request)

        # Every emitted content_block_delta that's a text_delta carries a non-empty string.
        for ev in emitted:
            if getattr(ev, "type", None) != "content_block_delta":
                continue
            delta = getattr(ev, "delta", None)
            if getattr(delta, "type", None) == "text_delta":
                assert getattr(delta, "text", "") != "", "emitted empty text_delta"

        # No content block's accumulated text is empty.
        indices = {
            getattr(ev, "index", -1)
            for ev in emitted
            if getattr(ev, "type", None) == "content_block_start"
        }
        for idx in indices:
            assert _text_of_block(emitted, idx) != "", f"block {idx} is empty"

    @pytest.mark.asyncio
    async def test_tool_use_rendered_as_labeled_text(self):
        """Tool-use blocks from any model are rendered as inert labeled text."""
        policy = MultiBackendPolicy(MultiBackendConfig(models=["primary", "extra"]))
        ctx = _context_with_credential()

        _install_streaming_clients(
            policy,
            {"extra": _FakeStreamingClient([_message_start("extra"), message_delta()])},
        )

        primary_events: list[MessageStreamEvent] = [
            _message_start("primary"),
            tool_start(index=0, tool_id="toolu_1", name="Bash"),
            tool_delta('{"command": "ls"}', index=0),
            block_stop(index=0),
            message_delta(),
        ]
        request: AnthropicRequest = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": True,
        }

        emitted = await _run_streaming(policy, ctx, primary_events, request)

        # No tool_use content blocks should be emitted — everything becomes text.
        for ev in emitted:
            if getattr(ev, "type", None) == "content_block_start":
                block = getattr(ev, "content_block", None)
                assert getattr(block, "type", None) == "text"

        # The rendered text appears somewhere in the output.
        joined = "".join(
            _text_of_block(emitted, getattr(e, "index", -1))
            for e in emitted
            if getattr(e, "type", None) == "content_block_start"
        )
        assert "[tool_use: Bash(" in joined
        assert '"command": "ls"' in joined


# =============================================================================
# Non-streaming
# =============================================================================


def _make_response(model: str, content_text: str, in_tokens: int = 3, out_tokens: int = 7) -> AnthropicResponse:
    return {
        "id": f"msg_{model}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": content_text}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
    }


class TestNonStreaming:
    @pytest.mark.asyncio
    async def test_aggregates_all_models_in_config_order(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a", "b", "c"]))
        ctx = _context_with_credential()

        _install_complete_clients(
            policy,
            {
                "b": _FakeCompleteClient(_make_response("b", "text-from-b", 10, 20)),
                "c": _FakeCompleteClient(_make_response("c", "text-from-c", 100, 200)),
            },
        )

        request: AnthropicRequest = {
            "model": "whatever",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50,
        }
        modified = await policy.on_anthropic_request(request, ctx)
        assert modified["model"] == "a"

        # The executor would now call the backend and pass the response to the hook.
        primary_response = _make_response("a", "text-from-a", 1, 2)
        aggregated = await policy.on_anthropic_response(primary_response, ctx)

        joined = "".join(block.get("text", "") for block in aggregated["content"] if block.get("type") == "text")
        assert "# a" in joined
        assert "# b" in joined
        assert "# c" in joined
        # Order in the combined text should match config order.
        assert joined.index("# a") < joined.index("# b") < joined.index("# c")
        assert "text-from-a" in joined
        assert "text-from-b" in joined
        assert "text-from-c" in joined

        assert aggregated["usage"]["input_tokens"] == 1 + 10 + 100
        assert aggregated["usage"]["output_tokens"] == 2 + 20 + 200
        assert aggregated["model"] == "multi[a,b,c]"

    @pytest.mark.asyncio
    async def test_failing_extra_rendered_as_error_block(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a", "b"]))
        ctx = _context_with_credential()

        _install_complete_clients(
            policy,
            {"b": _FakeCompleteClient(RuntimeError("boom"))},
        )

        request: AnthropicRequest = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        }
        await policy.on_anthropic_request(request, ctx)
        aggregated = await policy.on_anthropic_response(_make_response("a", "ok"), ctx)

        joined = "".join(b.get("text", "") for b in aggregated["content"] if b.get("type") == "text")
        assert "# b" in joined
        assert "**Error:**" in joined
        assert "boom" in joined

    @pytest.mark.asyncio
    async def test_tool_use_rendered_as_labeled_text_non_streaming(self):
        policy = MultiBackendPolicy(MultiBackendConfig(models=["a", "b"]))
        ctx = _context_with_credential()

        response_with_tool: AnthropicResponse = {
            "id": "msg_b",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "before-tool"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ],
            "model": "b",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        _install_complete_clients(policy, {"b": _FakeCompleteClient(response_with_tool)})

        request: AnthropicRequest = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        }
        await policy.on_anthropic_request(request, ctx)
        aggregated = await policy.on_anthropic_response(_make_response("a", "primary"), ctx)

        # All content blocks are text; no real tool_use forwarded.
        for block in aggregated["content"]:
            assert block["type"] == "text"

        joined = "".join(b["text"] for b in aggregated["content"])
        assert "[tool_use: Bash(" in joined
        assert '"command": "ls"' in joined
