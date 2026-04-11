"""Unit tests for SupplyChainGatePolicy.

These tests focus on the integration between the policy hooks, the mocked
OSV client, and the streaming output shape. The regex/severity helpers have
their own dedicated test module.

Critical streaming correctness tests live in ``TestStreamingShape``. These
are the tests that would have caught PR #536's non-monotonic-index bug, and
they are mandatory regression guards for the command-substitution design.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.policies.supply_chain_gate_policy import (
    SupplyChainGatePolicy,
    _dedupe_packages,
    _extract_bash_command,
    _extract_command_from_json,
    _GateState,
    _rewrite_command_in_input_json,
)
from luthien_proxy.policies.supply_chain_gate_utils import (
    OSVClient,
    PackageRef,
    Severity,
    SupplyChainGateConfig,
    VulnInfo,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

# =============================================================================
# Fakes & helpers
# =============================================================================


class _FakeOSVClient(OSVClient):
    """In-memory OSV client. Maps cache_key -> list[VulnInfo] or raises."""

    def __init__(
        self,
        responses: dict[str, list[VulnInfo]] | None = None,
        raise_for: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._responses = responses or {}
        self._raise_for = raise_for or set()
        self.calls: list[PackageRef] = []
        self.call_started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.concurrent_high_water = 0
        self._in_flight = 0
        self._lock = asyncio.Lock()

    async def query(self, package: PackageRef) -> list[VulnInfo]:
        self.calls.append(package)
        async with self._lock:
            self._in_flight += 1
            if self._in_flight > self.concurrent_high_water:
                self.concurrent_high_water = self._in_flight
        self.call_started.set()
        try:
            await self.release.wait()
            if package.name in self._raise_for:
                raise RuntimeError(f"OSV down for {package.name}")
            return list(self._responses.get(package.cache_key(), []))
        finally:
            async with self._lock:
                self._in_flight -= 1


def _make_policy(
    osv: OSVClient | None = None,
    config: dict[str, Any] | None = None,
) -> SupplyChainGatePolicy:
    cfg = SupplyChainGateConfig.model_validate(config or {})
    return SupplyChainGatePolicy(config=cfg, osv_client=osv or _FakeOSVClient())


def _make_context() -> PolicyContext:
    return PolicyContext.for_testing(transaction_id="test-txn")


def _critical(name: str) -> VulnInfo:
    return VulnInfo(id=f"GHSA-{name}", severity=Severity.CRITICAL)


def _medium(name: str) -> VulnInfo:
    return VulnInfo(id=f"GHSA-{name}-med", severity=Severity.MEDIUM)


def _tool_start(index: int, tool_id: str = "t1", name: str = "Bash") -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=tool_id, name=name, input={}),
    )


def _text_start(index: int) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=TextBlock(type="text", text=""),
    )


def _input_delta(index: int, partial_json: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=partial_json),
    )


def _text_delta(index: int, text: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=text),
    )


def _block_stop(index: int) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


async def _run_stream(
    policy: SupplyChainGatePolicy,
    events: list[MessageStreamEvent],
    context: PolicyContext,
) -> list[MessageStreamEvent]:
    out: list[MessageStreamEvent] = []
    for event in events:
        out.extend(await policy.on_anthropic_stream_event(event, context))
    return out


# =============================================================================
# Module-level helpers
# =============================================================================


class TestModuleHelpers:
    def test_extract_bash_command_variants(self):
        assert _extract_bash_command({"command": "ls"}) == "ls"
        assert _extract_bash_command({}) is None
        assert _extract_bash_command("ls") is None
        assert _extract_bash_command({"command": 5}) is None

    def test_extract_command_from_json_variants(self):
        assert _extract_command_from_json('{"command": "ls"}') == "ls"
        assert _extract_command_from_json("") is None
        assert _extract_command_from_json("{not json") is None

    def test_dedupe_packages(self):
        pkgs = [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "flask", None),
        ]
        assert _dedupe_packages(pkgs) == [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "flask", None),
        ]

    def test_dedupe_preserves_distinct_versions(self):
        pkgs = [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "requests", "2.30.0"),
        ]
        assert _dedupe_packages(pkgs) == pkgs

    def test_rewrite_command_in_input_json(self):
        out = _rewrite_command_in_input_json('{"command": "pip install foo", "timeout": 30}', "new")
        assert json.loads(out) == {"command": "new", "timeout": 30}

    def test_rewrite_command_malformed_fallback(self):
        out = _rewrite_command_in_input_json("not json", "new")
        assert json.loads(out) == {"command": "new"}


# =============================================================================
# Init / config
# =============================================================================


class TestInit:
    def test_default_config(self):
        policy = SupplyChainGatePolicy()
        assert policy._threshold is Severity.CRITICAL
        assert "Bash" in policy._bash_tool_names
        assert policy.short_policy_name == "SupplyChainGate"

    def test_custom_threshold_and_blocklist(self):
        policy = _make_policy(config={"severity_threshold": "medium", "explicit_blocklist": ["PyPI:foo:1.0"]})
        assert policy._threshold is Severity.MEDIUM
        # Stored in canonical form.
        assert "pypi:foo:1.0" in policy._blocklist

    def test_freeze_configured_state_passes(self):
        _make_policy().freeze_configured_state()


# =============================================================================
# Non-streaming on_anthropic_response
# =============================================================================


class TestNonStreamingResponse:
    @pytest.mark.asyncio
    async def test_passthrough_when_no_tool_use(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        response: dict[str, Any] = {"content": [{"type": "text", "text": "hi"}]}
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_passthrough_when_non_install_command(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo hi"}},
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_passthrough_when_no_advisory(self):
        osv = _FakeOSVClient(responses={"osv:PyPI:requests:2.31.0": []})
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install requests==2.31.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response
        assert len(osv.calls) == 1

    @pytest.mark.asyncio
    async def test_substitutes_critical_package(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is not response
        assert len(result["content"]) == 1  # no new blocks added
        tool_use = result["content"][0]
        assert tool_use["type"] == "tool_use"
        assert tool_use["id"] == "t1"
        new_command = tool_use["input"]["command"]
        assert new_command.startswith("sh -c '")
        assert "LUTHIEN BLOCKED" in new_command
        assert "litellm" in new_command
        assert "GHSA-LITELLM" in new_command
        # The new command is the failing sh script — not the original install.
        # The original command will appear inside the diagnostic message body
        # (so the LLM knows what it tried to run), but not as the runnable command.
        assert "exit 42" in new_command

    @pytest.mark.asyncio
    async def test_skips_below_threshold(self):
        osv = _FakeOSVClient(responses={"osv:PyPI:flask:3.0.0": [_medium("FLASK")]})
        policy = _make_policy(osv)  # default threshold CRITICAL
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install flask==3.0.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response

    @pytest.mark.asyncio
    async def test_threshold_lowered_to_medium(self):
        osv = _FakeOSVClient(responses={"osv:PyPI:flask:3.0.0": [_medium("FLASK")]})
        policy = _make_policy(osv, {"severity_threshold": "medium"})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install flask==3.0.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert "LUTHIEN BLOCKED" in result["content"][0]["input"]["command"]

    @pytest.mark.asyncio
    async def test_ignores_non_bash_tool(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"command": "pip install litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response
        assert osv.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["warn", "allow"])
    async def test_warn_and_allow_modes_pass_through_on_osv_error(self, mode: str):
        osv = _FakeOSVClient(raise_for={"litellm"})
        policy = _make_policy(osv, {"osv_fail_mode": mode})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response

    @pytest.mark.asyncio
    async def test_block_mode_substitutes_on_osv_error(self):
        osv = _FakeOSVClient(raise_for={"litellm"})
        policy = _make_policy(osv, {"osv_fail_mode": "block"})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert "LUTHIEN BLOCKED" in result["content"][0]["input"]["command"]

    @pytest.mark.asyncio
    async def test_explicit_blocklist_substitutes_without_osv(self):
        osv = _FakeOSVClient()  # empty — would normally allow
        policy = _make_policy(
            osv,
            {
                "explicit_blocklist": ["PyPI:litellm:1.59.0"],
                "severity_threshold": "critical",
            },
        )
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        substituted = result["content"][0]["input"]["command"]
        assert "LUTHIEN BLOCKED" in substituted
        assert "explicit blocklist" in substituted

    @pytest.mark.asyncio
    async def test_lockfile_install_substituted_with_dry_run(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)  # block_lockfile_installs default True
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "npm ci"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        substituted = result["content"][0]["input"]["command"]
        assert "sh -c" in substituted
        assert "npm ci --dry-run" in substituted
        assert "LUTHIEN" in substituted
        # Short-circuits OSV: no packages parsed from `npm ci`.
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_lockfile_passthrough_when_disabled(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv, {"block_lockfile_installs": False})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "npm ci"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response


# =============================================================================
# STREAMING — CRITICAL CORRECTNESS TESTS
# =============================================================================


class TestStreamingShape:
    """Mandatory regression tests for the stream-shape invariant.

    These tests verify the stream output respects the Anthropic protocol:
      - content_block_start indices are strictly monotonic
      - the number of content_block_start events equals the upstream count
      - tool_use blocks keep their original index when rewritten
    """

    @pytest.mark.asyncio
    async def test_flagged_tool_use_preserves_block_index(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        ctx = _make_context()
        events = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install litellm==1.59.0"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        starts = [e for e in out if isinstance(e, RawContentBlockStartEvent)]
        assert len(starts) == 1
        assert starts[0].index == 0

    @pytest.mark.asyncio
    async def test_flagged_tool_use_preserves_block_count(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        ctx = _make_context()
        events = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install litellm==1.59.0"}'),
            _block_stop(0),
        ]
        upstream_start_count = sum(1 for e in events if isinstance(e, RawContentBlockStartEvent))
        out = await _run_stream(policy, events, ctx)
        emitted_start_count = sum(1 for e in out if isinstance(e, RawContentBlockStartEvent))
        assert emitted_start_count == upstream_start_count

    @pytest.mark.asyncio
    async def test_two_flagged_tool_uses_preserve_indices(self):
        osv = _FakeOSVClient(
            responses={
                "osv:PyPI:litellm:1.59.0": [_critical("LITELLM")],
                "osv:npm:axios:1.6.8": [_critical("AXIOS")],
            },
        )
        policy = _make_policy(osv)
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0, tool_id="t0"),
            _input_delta(0, '{"command": "pip install litellm==1.59.0"}'),
            _block_stop(0),
            _text_start(1),
            _text_delta(1, "here you go"),
            _block_stop(1),
            _tool_start(2, tool_id="t2"),
            _input_delta(2, '{"command": "npm install axios@1.6.8"}'),
            _block_stop(2),
        ]
        out = await _run_stream(policy, events, ctx)
        starts = [e for e in out if isinstance(e, RawContentBlockStartEvent)]
        # One start per upstream block.
        assert [s.index for s in starts] == [0, 1, 2]
        # Monotonic.
        indices = [e.index for e in out if hasattr(e, "index")]
        assert indices == sorted(indices)
        # Text block at index 1 is untouched (rendered as TextBlock).
        assert any(isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, TextBlock) for e in out)

    @pytest.mark.asyncio
    async def test_monotonic_block_start_across_mixed_stream(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _text_start(0),
            _text_delta(0, "thinking..."),
            _block_stop(0),
            _tool_start(1, tool_id="flagged"),
            _input_delta(1, '{"command": "pip install litellm==1.59.0"}'),
            _block_stop(1),
            _text_start(2),
            _text_delta(2, "okay"),
            _block_stop(2),
            _tool_start(3, tool_id="unflagged"),
            _input_delta(3, '{"command": "echo hi"}'),
            _block_stop(3),
            _text_start(4),
            _text_delta(4, "done"),
            _block_stop(4),
        ]
        out = await _run_stream(policy, events, ctx)
        starts = [e.index for e in out if isinstance(e, RawContentBlockStartEvent)]
        assert starts == [0, 1, 2, 3, 4]
        assert starts == sorted(starts)

    @pytest.mark.asyncio
    async def test_flagged_tool_use_rewrites_command_field(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install litellm==1.59.0"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        deltas = [e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta)]
        assert len(deltas) == 1
        partial = deltas[0].delta.partial_json  # type: ignore[union-attr]
        assert "sh -c" in partial
        assert "LUTHIEN BLOCKED" in partial
        # The original command's first token `pip` must not remain as a
        # runnable command. It can appear in the redacted-original quoted
        # display line, so we assert the runnable command is overwritten
        # (the `command` JSON value begins with sh -c).
        parsed = json.loads(partial)
        assert parsed["command"].startswith("sh -c")


# =============================================================================
# STREAMING — buffering + tool_use shape
# =============================================================================


class TestStreamingBasics:
    @pytest.mark.asyncio
    async def test_passthrough_text_block(self):
        policy = _make_policy()
        ctx = _make_context()
        event = _text_start(0)
        out = await policy.on_anthropic_stream_event(event, ctx)  # type: ignore[arg-type]
        assert out == [event]

    @pytest.mark.asyncio
    async def test_non_bash_tool_passthrough(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        event = _tool_start(0, name="Read")
        out = await policy.on_anthropic_stream_event(event, ctx)  # type: ignore[arg-type]
        assert out == [event]
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_buffers_bash_tool_use_until_stop(self):
        osv = _FakeOSVClient(responses={"osv:PyPI:requests:2.31.0": []})
        policy = _make_policy(osv)
        ctx = _make_context()

        out_start = await policy.on_anthropic_stream_event(_tool_start(0), ctx)  # type: ignore[arg-type]
        assert out_start == []

        out_delta = await policy.on_anthropic_stream_event(
            _input_delta(0, '{"command": "pip install requests==2.31.0"}'),
            ctx,  # type: ignore[arg-type]
        )
        assert out_delta == []

        out_stop = await policy.on_anthropic_stream_event(_block_stop(0), ctx)  # type: ignore[arg-type]
        assert len(out_stop) == 3
        types = [type(e).__name__ for e in out_stop]
        assert types == [
            "RawContentBlockStartEvent",
            "RawContentBlockDeltaEvent",
            "RawContentBlockStopEvent",
        ]

    @pytest.mark.asyncio
    async def test_unflagged_tool_use_preserves_command(self):
        osv = _FakeOSVClient(responses={"osv:PyPI:requests:2.31.0": []})
        policy = _make_policy(osv)
        ctx = _make_context()
        events = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install requests==2.31.0"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        delta = next(e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta))
        parsed = json.loads(delta.delta.partial_json)  # type: ignore[union-attr]
        assert parsed["command"] == "pip install requests==2.31.0"

    @pytest.mark.asyncio
    async def test_stream_complete_flushes_orphan(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        await policy.on_anthropic_stream_event(_tool_start(0), ctx)  # type: ignore[arg-type]
        await policy.on_anthropic_stream_event(
            _input_delta(0, '{"command": "echo hi"}'),
            ctx,  # type: ignore[arg-type]
        )
        # No stop event arrived.
        emissions = await policy.on_anthropic_stream_complete(ctx)
        assert len(emissions) == 3  # start + delta + stop
        # State cleared.
        state = ctx.get_request_state(policy, _GateState, _GateState)
        assert state.buffered_tool_uses == {}

    @pytest.mark.asyncio
    async def test_stream_complete_empty(self):
        policy = _make_policy()
        ctx = _make_context()
        assert await policy.on_anthropic_stream_complete(ctx) == []

    @pytest.mark.asyncio
    async def test_streaming_policy_complete_pops_state(self):
        policy = _make_policy()
        ctx = _make_context()
        ctx.get_request_state(policy, _GateState, _GateState)
        await policy.on_anthropic_streaming_policy_complete(ctx)
        assert ctx.pop_request_state(policy, _GateState) is None

    @pytest.mark.asyncio
    async def test_partial_json_chunks_accumulate(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("L")]},
        )
        policy = _make_policy(osv)
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"comm'),
            _input_delta(0, 'and": "pip install lit'),
            _input_delta(0, 'ellm==1.59.0"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        # Critical vuln flagged → substituted.
        delta = next(e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta))
        parsed = json.loads(delta.delta.partial_json)  # type: ignore[union-attr]
        assert parsed["command"].startswith("sh -c")


# =============================================================================
# Concurrency, dedupe, cache
# =============================================================================


class TestConcurrentLookups:
    @pytest.mark.asyncio
    async def test_semaphore_caps_concurrency(self):
        osv = _FakeOSVClient()
        osv.release.clear()
        policy = _make_policy(osv, {"max_concurrent_lookups": 2})
        ctx = _make_context()
        task = asyncio.create_task(policy._rewrite_if_needed("pip install a==1 b==1 c==1 d==1 e==1", ctx))
        await osv.call_started.wait()
        await asyncio.sleep(0.05)
        osv.release.set()
        await task
        assert osv.concurrent_high_water <= 2
        assert len(osv.calls) == 5

    @pytest.mark.asyncio
    async def test_dedupes_before_lookup(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        await policy._rewrite_if_needed("pip install requests==2.31.0 requests==2.31.0", ctx)
        assert len(osv.calls) == 1


class _StubPolicyCache:
    """Minimal in-memory PolicyCache stand-in for tests."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.get_calls = 0
        self.put_calls = 0

    async def get(self, key: str) -> Any:
        self.get_calls += 1
        return self.store.get(key)

    async def put(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.put_calls += 1
        self.store[key] = value


def _ctx_with_cache(cache: _StubPolicyCache) -> PolicyContext:
    return PolicyContext.for_testing(
        transaction_id="test-txn",
        policy_cache_factory=lambda _name: cast(Any, cache),
    )


class TestCache:
    @pytest.mark.asyncio
    async def test_cache_miss_then_hit(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("L")]},
        )
        policy = _make_policy(osv)
        cache = _StubPolicyCache()
        ctx = _ctx_with_cache(cache)

        await policy._rewrite_if_needed("pip install litellm==1.59.0", ctx)
        assert len(osv.calls) == 1
        assert cache.put_calls == 1

        await policy._rewrite_if_needed("pip install litellm==1.59.0", ctx)
        assert len(osv.calls) == 1  # cached
        assert cache.get_calls >= 2

    @pytest.mark.asyncio
    async def test_error_cached_negatively(self):
        osv = _FakeOSVClient(raise_for={"litellm"})
        policy = _make_policy(osv)
        cache = _StubPolicyCache()
        ctx = _ctx_with_cache(cache)

        await policy._rewrite_if_needed("pip install litellm==1.59.0", ctx)
        key = PackageRef("PyPI", "litellm", "1.59.0").cache_key()
        assert cache.store[key].get("error") is not None

        await policy._rewrite_if_needed("pip install litellm==1.59.0", ctx)
        assert len(osv.calls) == 1  # served from negative cache


# =============================================================================
# Major #1 — unexpected-delta at buffered index flushes cleanly
# =============================================================================


class TestBufferedBlockFlushOnUnexpectedDelta:
    @pytest.mark.asyncio
    async def test_text_delta_at_buffered_tool_use_flushes_block(self):
        # Construct a fake stream where a tool_use is buffered (content_block_start
        # was swallowed) and then an unexpected TextDelta arrives at the same
        # index. Downstream must see the flushed content_block_start before the
        # unexpected delta — otherwise the delta is orphaned.
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()

        start_out = await policy.on_anthropic_stream_event(_tool_start(0), ctx)  # type: ignore[arg-type]
        assert start_out == []  # buffered — nothing emitted yet

        # Accumulate some input_json first so we have state to flush.
        await policy.on_anthropic_stream_event(
            _input_delta(0, '{"command": "pip install foo'),
            ctx,  # type: ignore[arg-type]
        )

        # Unexpected delta type at the buffered index.
        text_event = _text_delta(0, "oops")
        out = await policy.on_anthropic_stream_event(text_event, ctx)  # type: ignore[arg-type]

        # Expected: [flushed start, flushed accumulated json, unexpected delta]
        assert len(out) == 3
        assert isinstance(out[0], RawContentBlockStartEvent)
        assert isinstance(out[1], RawContentBlockDeltaEvent)
        assert out[2] is text_event

        # Future events at this index must pass through without buffering.
        post_event = _text_delta(0, "more")
        out2 = await policy.on_anthropic_stream_event(post_event, ctx)  # type: ignore[arg-type]
        assert out2 == [post_event]

        # And the state dictionary no longer holds the block.
        state = ctx.get_request_state(policy, _GateState, _GateState)
        assert 0 not in state.buffered_tool_uses


# =============================================================================
# Fatal #4 — wrapper commands
# =============================================================================


class TestWrapperCommandIntegration:
    @pytest.mark.asyncio
    async def test_docker_run_does_not_trigger_osv_lookup(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "docker run --rm python:3.11 pip install litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        # Wrapper suppresses extraction — no substitution, no OSV call.
        assert result is response
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_sudo_still_substitutes(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "sudo pip install litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert "LUTHIEN BLOCKED" in result["content"][0]["input"]["command"]


# =============================================================================
# Fatal #5 — line continuation normalization
# =============================================================================


class TestLineContinuationIntegration:
    @pytest.mark.asyncio
    async def test_continuation_still_triggers_substitution(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.59.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install \\\n  litellm==1.59.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert "LUTHIEN BLOCKED" in result["content"][0]["input"]["command"]
        assert len(osv.calls) == 1
        assert osv.calls[0].name == "litellm"


# =============================================================================
# Fatal #1 — lockfile dry-run threads the filename
# =============================================================================


class TestLockfileDryRunFilenameThreading:
    @pytest.mark.asyncio
    async def test_dev_requirements_filename_in_substitute(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install -r dev-requirements.txt"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        substituted = result["content"][0]["input"]["command"]
        assert "dev-requirements.txt" in substituted
        # No spurious bare `requirements.txt` argument.
        assert "'requirements.txt'" not in substituted

    @pytest.mark.asyncio
    async def test_yarn_uses_explain_refuse_not_dry_run(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "yarn install --frozen-lockfile"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        substituted = result["content"][0]["input"]["command"]
        # Fatal #2: yarn must not emit a fake --mode=skip-build dry-run.
        assert "--mode=skip-build" not in substituted
        assert "LUTHIEN BLOCKED" in substituted or "cannot be safely previewed" in substituted


# =============================================================================
# Fatal #3 — blocklist canonicalization through the policy
# =============================================================================


class TestBlocklistCanonicalizationPolicy:
    @pytest.mark.asyncio
    async def test_pypi_case_variant_matches_blocklist(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv, {"explicit_blocklist": ["PyPI:Pillow:10.0.0"]})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install pillow==10.0.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        substituted = result["content"][0]["input"]["command"]
        assert "LUTHIEN BLOCKED" in substituted
        assert "explicit blocklist" in substituted

    @pytest.mark.asyncio
    async def test_npm_scoped_case_variant_matches_blocklist(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv, {"explicit_blocklist": ["npm:@MyScope/Pkg:1.0"]})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "npm install @myscope/pkg@1.0"},
                },
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        substituted = result["content"][0]["input"]["command"]
        assert "LUTHIEN BLOCKED" in substituted
