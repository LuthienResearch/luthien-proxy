"""Unit tests for SupplyChainAdvisoryPolicy.

These tests focus on the integration between the policy hooks, the OSV
client (mocked), and the per-request state. The regex/severity/redaction
helpers have their own dedicated test module.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.policies.supply_chain_advisory_policy import (
    SupplyChainAdvisoryPolicy,
    _AdvisoryState,
    _collect_tool_result_texts,
    _dedupe_packages,
    _extract_bash_command,
    _extract_command_from_json,
    _prepend_system_warning,
)
from luthien_proxy.policies.supply_chain_advisory_utils import (
    OSVClient,
    PackageRef,
    Severity,
    SupplyChainAdvisoryConfig,
    VulnInfo,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

# =============================================================================
# Fakes
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
    osv: OSVClient,
    config: dict[str, Any] | None = None,
) -> SupplyChainAdvisoryPolicy:
    cfg = SupplyChainAdvisoryConfig.model_validate(config or {})
    return SupplyChainAdvisoryPolicy(config=cfg, osv_client=osv)


def _make_context() -> PolicyContext:
    return PolicyContext.for_testing(transaction_id="test-txn")


def _critical(name: str) -> VulnInfo:
    return VulnInfo(id=f"GHSA-{name}", summary=f"{name} bad", severity=Severity.CRITICAL)


def _medium(name: str) -> VulnInfo:
    return VulnInfo(id=f"GHSA-{name}-med", summary=f"{name} mild", severity=Severity.MEDIUM)


# =============================================================================
# Module-level helpers
# =============================================================================


class TestExtractBashCommand:
    def test_dict(self):
        assert _extract_bash_command({"command": "ls"}) == "ls"

    def test_missing(self):
        assert _extract_bash_command({}) is None

    def test_non_dict(self):
        assert _extract_bash_command("ls") is None

    def test_non_string_command(self):
        assert _extract_bash_command({"command": 5}) is None


class TestExtractCommandFromJson:
    def test_valid(self):
        assert _extract_command_from_json('{"command": "ls"}') == "ls"

    def test_empty(self):
        assert _extract_command_from_json("") is None

    def test_invalid(self):
        assert _extract_command_from_json("{not json") is None


class TestDedupePackages:
    def test_dedupe(self):
        pkgs = [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "flask", None),
        ]
        result = _dedupe_packages(pkgs)
        assert result == [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "flask", None),
        ]

    def test_different_versions_kept(self):
        pkgs = [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "requests", "2.30.0"),
        ]
        assert _dedupe_packages(pkgs) == pkgs


class TestCollectToolResultTexts:
    def test_string_content(self):
        msg: dict[str, Any] = {
            "role": "user",
            "content": [{"type": "tool_result", "content": "requests==2.31.0"}],
        }
        assert _collect_tool_result_texts(msg) == ["requests==2.31.0"]  # type: ignore[arg-type]

    def test_list_content(self):
        msg: dict[str, Any] = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": [
                        {"type": "text", "text": "axios@1.6.8"},
                        {"type": "text", "text": "left-pad"},
                    ],
                }
            ],
        }
        assert _collect_tool_result_texts(msg) == ["axios@1.6.8", "left-pad"]  # type: ignore[arg-type]

    def test_no_tool_result(self):
        msg: dict[str, Any] = {"role": "user", "content": [{"type": "text", "text": "hi"}]}
        assert _collect_tool_result_texts(msg) == []  # type: ignore[arg-type]

    def test_string_message_content(self):
        msg: dict[str, Any] = {"role": "user", "content": "hi"}
        assert _collect_tool_result_texts(msg) == []  # type: ignore[arg-type]


class TestPrependSystemWarning:
    def test_none(self):
        assert _prepend_system_warning(None, "warn") == "warn"

    def test_string(self):
        assert _prepend_system_warning("you are helpful", "warn") == "warn\n\nyou are helpful"

    def test_empty_string(self):
        assert _prepend_system_warning("", "warn") == "warn"

    def test_list_blocks(self):
        existing = [{"type": "text", "text": "be helpful"}]
        result = _prepend_system_warning(existing, "warn")  # type: ignore[arg-type]
        assert isinstance(result, list)
        assert result[0] == {"type": "text", "text": "warn"}
        assert result[1] == {"type": "text", "text": "be helpful"}


# =============================================================================
# Init / config
# =============================================================================


class TestInit:
    def test_default_config(self):
        policy = SupplyChainAdvisoryPolicy()
        assert policy._threshold is Severity.HIGH
        assert "Bash" in policy._bash_tool_names

    def test_custom_threshold(self):
        policy = _make_policy(_FakeOSVClient(), {"advisory_severity_threshold": "MEDIUM"})
        assert policy._threshold is Severity.MEDIUM

    def test_freeze_configured_state_passes(self):
        policy = _make_policy(_FakeOSVClient())
        # freeze_configured_state must succeed: no mutable containers on the
        # instance. The frozenset/tuple choices in __init__ are intentional.
        policy.freeze_configured_state()

    def test_short_policy_name(self):
        assert SupplyChainAdvisoryPolicy().short_policy_name == "SupplyChainAdvisory"


# =============================================================================
# on_anthropic_response (non-streaming)
# =============================================================================


class TestOnAnthropicResponse:
    @pytest.mark.asyncio
    async def test_passthrough_when_no_tool_use(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        response: dict[str, Any] = {"content": [{"type": "text", "text": "hi"}]}
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_passthrough_when_no_install_command(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "echo hello"},
                }
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
                }
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response
        assert len(osv.calls) == 1

    @pytest.mark.asyncio
    async def test_injects_advisory_for_critical_package(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.48.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.48.0"},
                }
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        # Advisory text block inserted before the tool_use block.
        assert result["content"][0]["type"] == "text"
        assert "SUPPLY CHAIN ADVISORY" in result["content"][0]["text"]
        assert "litellm" in result["content"][0]["text"]
        assert "GHSA-LITELLM" in result["content"][0]["text"]
        # The original tool_use block is preserved unchanged.
        assert result["content"][1]["type"] == "tool_use"

    @pytest.mark.asyncio
    async def test_skips_below_threshold(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:flask:3.0.0": [_medium("FLASK")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install flask==3.0.0"},
                }
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response

    @pytest.mark.asyncio
    async def test_threshold_lowered_to_medium(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:flask:3.0.0": [_medium("FLASK")]},
        )
        policy = _make_policy(osv, {"advisory_severity_threshold": "MEDIUM"})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install flask==3.0.0"},
                }
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result["content"][0]["type"] == "text"
        assert "MEDIUM" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_ignores_non_bash_tool(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.48.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"command": "pip install litellm==1.48.0"},
                }
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_warn_on_osv_error(self):
        osv = _FakeOSVClient(raise_for={"litellm"})
        policy = _make_policy(osv, {"warn_on_osv_error": True})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.48.0"},
                }
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result["content"][0]["type"] == "text"
        assert "OSV lookup failed" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_silent_on_osv_error_when_disabled(self):
        osv = _FakeOSVClient(raise_for={"litellm"})
        policy = _make_policy(osv, {"warn_on_osv_error": False})
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.48.0"},
                }
            ],
        }
        result = await policy.on_anthropic_response(response, _make_context())  # type: ignore[arg-type]
        assert result is response


# =============================================================================
# on_anthropic_request (incoming tool_results)
# =============================================================================


class TestOnAnthropicRequest:
    @pytest.mark.asyncio
    async def test_passthrough_no_messages(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        request: dict[str, Any] = {"messages": [], "model": "claude", "max_tokens": 100}
        result = await policy.on_anthropic_request(request, _make_context())  # type: ignore[arg-type]
        assert result is request

    @pytest.mark.asyncio
    async def test_passthrough_assistant_last(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        request: dict[str, Any] = {
            "messages": [{"role": "assistant", "content": "hi"}],
            "model": "claude",
            "max_tokens": 100,
        }
        result = await policy.on_anthropic_request(request, _make_context())  # type: ignore[arg-type]
        assert result is request

    @pytest.mark.asyncio
    async def test_passthrough_no_install_in_tool_result(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        request: dict[str, Any] = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "hello world"}],
                }
            ],
            "model": "claude",
            "max_tokens": 100,
        }
        result = await policy.on_anthropic_request(request, _make_context())  # type: ignore[arg-type]
        assert result is request
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_warns_on_pip_freeze_with_vulnerable_pkg(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.48.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        request: dict[str, Any] = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "content": "litellm==1.48.0\nnumpy==1.26.0",
                        }
                    ],
                }
            ],
            "model": "claude",
            "max_tokens": 100,
        }
        result = await policy.on_anthropic_request(request, _make_context())  # type: ignore[arg-type]
        assert result is not request
        system = result.get("system")
        assert system is not None
        assert "SUPPLY CHAIN ADVISORY" in (system if isinstance(system, str) else system[0]["text"])

    @pytest.mark.asyncio
    async def test_prepends_to_existing_system(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.48.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        request: dict[str, Any] = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": "litellm==1.48.0"},
                    ],
                }
            ],
            "system": "You are helpful.",
            "model": "claude",
            "max_tokens": 100,
        }
        result = await policy.on_anthropic_request(request, _make_context())  # type: ignore[arg-type]
        system = result.get("system")
        assert isinstance(system, str)
        assert "SUPPLY CHAIN ADVISORY" in system
        assert "You are helpful." in system


# =============================================================================
# Streaming
# =============================================================================


def _start_event(index: int, block: ToolUseBlock | TextBlock) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(type="content_block_start", index=index, content_block=block)


def _delta_event(index: int, delta: InputJSONDelta | TextDelta) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=delta)


def _stop_event(index: int) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


class TestStreaming:
    @pytest.mark.asyncio
    async def test_passthrough_text_block(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        text_event = _start_event(0, TextBlock(type="text", text=""))
        out = await policy.on_anthropic_stream_event(text_event, ctx)  # type: ignore[arg-type]
        assert out == [text_event]

    @pytest.mark.asyncio
    async def test_buffers_bash_tool_use_until_stop(self):
        osv = _FakeOSVClient(responses={"osv:PyPI:requests:2.31.0": []})
        policy = _make_policy(osv)
        ctx = _make_context()
        tool_block = ToolUseBlock(type="tool_use", id="t1", name="Bash", input={})

        out_start = await policy.on_anthropic_stream_event(_start_event(0, tool_block), ctx)  # type: ignore[arg-type]
        assert out_start == []  # suppressed

        out_delta = await policy.on_anthropic_stream_event(
            _delta_event(
                0, InputJSONDelta(type="input_json_delta", partial_json='{"command": "pip install requests==2.31.0"}')
            ),  # type: ignore[arg-type]
            ctx,
        )
        assert out_delta == []  # suppressed

        out_stop = await policy.on_anthropic_stream_event(_stop_event(0), ctx)  # type: ignore[arg-type]
        # No advisory: re-emit start, delta, stop.
        assert len(out_stop) == 3
        types = [type(e).__name__ for e in out_stop]
        assert types == [
            "RawContentBlockStartEvent",
            "RawContentBlockDeltaEvent",
            "RawContentBlockStopEvent",
        ]

    @pytest.mark.asyncio
    async def test_advisory_injected_when_critical_match(self):
        osv = _FakeOSVClient(
            responses={"osv:PyPI:litellm:1.48.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        ctx = _make_context()
        tool_block = ToolUseBlock(type="tool_use", id="t1", name="Bash", input={})

        await policy.on_anthropic_stream_event(_start_event(0, tool_block), ctx)  # type: ignore[arg-type]
        await policy.on_anthropic_stream_event(
            _delta_event(
                0, InputJSONDelta(type="input_json_delta", partial_json='{"command": "pip install litellm==1.48.0"}')
            ),  # type: ignore[arg-type]
            ctx,
        )
        out_stop = await policy.on_anthropic_stream_event(_stop_event(0), ctx)  # type: ignore[arg-type]
        # Advisory (3 events: start/delta/stop) + tool_use (start/delta/stop).
        assert len(out_stop) == 6
        # Advisory comes first.
        first = out_stop[0]
        assert isinstance(first, RawContentBlockStartEvent)
        assert isinstance(first.content_block, TextBlock)
        # The text delta carries the advisory body.
        delta_evt = out_stop[1]
        assert isinstance(delta_evt, RawContentBlockDeltaEvent)
        assert isinstance(delta_evt.delta, TextDelta)
        assert "SUPPLY CHAIN ADVISORY" in delta_evt.delta.text
        assert "litellm" in delta_evt.delta.text

    @pytest.mark.asyncio
    async def test_non_bash_tool_passthrough(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        tool_block = ToolUseBlock(type="tool_use", id="t1", name="Read", input={})
        evt = _start_event(0, tool_block)
        out = await policy.on_anthropic_stream_event(evt, ctx)  # type: ignore[arg-type]
        assert out == [evt]
        assert osv.calls == []

    @pytest.mark.asyncio
    async def test_stream_complete_flushes_orphan(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        tool_block = ToolUseBlock(type="tool_use", id="t1", name="Bash", input={})
        await policy.on_anthropic_stream_event(_start_event(0, tool_block), ctx)  # type: ignore[arg-type]
        await policy.on_anthropic_stream_event(
            _delta_event(0, InputJSONDelta(type="input_json_delta", partial_json='{"command": "echo hi"}')),  # type: ignore[arg-type]
            ctx,
        )
        # No stop event arrived; stream complete should flush.
        emissions = await policy.on_anthropic_stream_complete(ctx)
        assert len(emissions) == 2  # start + delta
        # Orphan map cleared.
        state = ctx.get_request_state(policy, _AdvisoryState, _AdvisoryState)
        assert state.buffered_tool_uses == {}

    @pytest.mark.asyncio
    async def test_stream_complete_empty(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        emissions = await policy.on_anthropic_stream_complete(ctx)
        assert emissions == []

    @pytest.mark.asyncio
    async def test_streaming_policy_complete_pops_state(self):
        osv = _FakeOSVClient()
        policy = _make_policy(osv)
        ctx = _make_context()
        # Force state creation.
        ctx.get_request_state(policy, _AdvisoryState, _AdvisoryState)
        await policy.on_anthropic_streaming_policy_complete(ctx)
        # State popped.
        assert ctx.pop_request_state(policy, _AdvisoryState) is None


# =============================================================================
# Concurrent lookups / semaphore
# =============================================================================


class TestConcurrentLookups:
    @pytest.mark.asyncio
    async def test_semaphore_caps_concurrency(self):
        # Five packages, max_concurrent_lookups=2 — high water must not exceed 2.
        osv = _FakeOSVClient()
        osv.release.clear()  # block all calls until we let them go
        policy = _make_policy(osv, {"max_concurrent_lookups": 2})
        ctx = _make_context()
        command = "pip install a==1 b==1 c==1 d==1 e==1"
        task = asyncio.create_task(policy._advisory_for_command(command, ctx))
        # Wait for at least one call to start, then a tiny moment for the
        # semaphore to settle, then release everything.
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
        # Same package mentioned twice.
        await policy._advisory_for_command("pip install requests==2.31.0 requests==2.31.0", ctx)
        assert len(osv.calls) == 1


# =============================================================================
# Cache integration
# =============================================================================


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
            responses={"osv:PyPI:litellm:1.48.0": [_critical("LITELLM")]},
        )
        policy = _make_policy(osv)
        cache = _StubPolicyCache()
        ctx = _ctx_with_cache(cache)

        await policy._advisory_for_command("pip install litellm==1.48.0", ctx)
        assert len(osv.calls) == 1
        assert cache.put_calls == 1

        # Second call with the same context should hit the cache.
        await policy._advisory_for_command("pip install litellm==1.48.0", ctx)
        assert len(osv.calls) == 1  # no new OSV call
        assert cache.get_calls >= 2

    @pytest.mark.asyncio
    async def test_error_cached_negatively(self):
        osv = _FakeOSVClient(raise_for={"litellm"})
        policy = _make_policy(osv)
        cache = _StubPolicyCache()
        ctx = _ctx_with_cache(cache)

        await policy._advisory_for_command("pip install litellm==1.48.0", ctx)
        # Error stored under the key.
        key = PackageRef("PyPI", "litellm", "1.48.0").cache_key()
        assert cache.store[key].get("error") is not None

        # Second call hits the cached error and does NOT re-query.
        await policy._advisory_for_command("pip install litellm==1.48.0", ctx)
        assert len(osv.calls) == 1
