"""Unit tests for :class:`SupplyChainBlocklistPolicy`.

The critical streaming-correctness regression suite lives in
``TestStreamingShape`` below. These exist because prior attempts (#536, #540)
shipped streaming-protocol violations that only manifested as downstream
parsing failures. Each test asserts specific ``content_block_start`` indices
and counts the starts emitted relative to the upstream shape — never "events
were emitted" alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

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

from luthien_proxy.policies.supply_chain_blocklist_policy import (
    SupplyChainBlocklistPolicy,
)
from luthien_proxy.policies.supply_chain_blocklist_utils import (
    ECOSYSTEM_NPM,
    ECOSYSTEM_PYPI,
    AffectedRange,
    BlocklistEntry,
    OSVFetchResult,
    SupplyChainBlocklistConfig,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.utils import db

# =============================================================================
# Test helpers
# =============================================================================


def _entry(
    name: str = "litellm",
    ecosystem: str = ECOSYSTEM_PYPI,
    cve: str = "CVE-2024-0001",
    introduced: str | None = None,
    fixed: str | None = "1.6.9",
    last_affected: str | None = None,
) -> BlocklistEntry:
    return BlocklistEntry(
        ecosystem=ecosystem,
        canonical_name=name,
        cve_id=cve,
        severity="CRITICAL",
        range=AffectedRange(introduced=introduced, fixed=fixed, last_affected=last_affected),
    )


def _make_policy(entries: list[BlocklistEntry] | None = None) -> SupplyChainBlocklistPolicy:
    policy = SupplyChainBlocklistPolicy()
    if entries:
        policy.set_index_for_testing(entries)
    return policy


def _make_context() -> PolicyContext:
    return PolicyContext.for_testing()


def _tool_start(index: int, name: str = "Bash", tool_id: str = "toolu_x") -> RawContentBlockStartEvent:
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


def _text_delta(index: int, text: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=text),
    )


def _input_delta(index: int, partial_json: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=partial_json),
    )


def _block_stop(index: int) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


async def _run_stream(
    policy: SupplyChainBlocklistPolicy,
    events: list[MessageStreamEvent],
    ctx: PolicyContext,
) -> list[MessageStreamEvent]:
    out: list[MessageStreamEvent] = []
    for e in events:
        out.extend(await policy.on_anthropic_stream_event(e, ctx))
    return out


# =============================================================================
# Config + instantiation
# =============================================================================


class TestConstruction:
    def test_default_config(self) -> None:
        p = SupplyChainBlocklistPolicy()
        assert p.short_policy_name == "SupplyChainBlocklist"
        assert p.index_size == 0

    def test_freeze_configured_state(self) -> None:
        p = SupplyChainBlocklistPolicy()
        p.freeze_configured_state()  # Must not raise.

    def test_custom_config_as_dict(self) -> None:
        p = SupplyChainBlocklistPolicy(config={"poll_interval_seconds": 60.0})
        assert p.config.poll_interval_seconds == 60.0

    def test_custom_config_as_model(self) -> None:
        p = SupplyChainBlocklistPolicy(config=SupplyChainBlocklistConfig(poll_interval_seconds=30.0))
        assert p.config.poll_interval_seconds == 30.0


# =============================================================================
# Scheduler integration (stub)
# =============================================================================


class _FakeScheduler:
    """In-memory scheduler used by tests.

    Captures callbacks and exposes a ``trigger`` method for deterministic
    invocation so tests never rely on wall-clock timing.
    """

    def __init__(self) -> None:
        self.callbacks: dict[str, Callable[[], Awaitable[None]]] = {}
        self.registrations: list[dict[str, Any]] = []

    def schedule(
        self,
        name: str,
        interval_seconds: float,
        callback: Callable[[], Awaitable[None]],
        jitter_seconds: float = 0.0,
        run_immediately: bool = False,
    ) -> None:
        self.callbacks[name] = callback
        self.registrations.append(
            {
                "name": name,
                "interval": interval_seconds,
                "jitter": jitter_seconds,
                "run_immediately": run_immediately,
            }
        )

    async def trigger(self, name: str) -> None:
        await self.callbacks[name]()


class TestSchedulerRegistration:
    def test_register_via_init(self) -> None:
        scheduler = _FakeScheduler()
        policy = SupplyChainBlocklistPolicy(scheduler=scheduler)
        assert len(scheduler.registrations) == 1
        reg = scheduler.registrations[0]
        assert reg["name"].startswith("SupplyChainBlocklist")
        assert reg["run_immediately"] is True
        assert reg["interval"] == policy.config.poll_interval_seconds
        assert reg["jitter"] == policy.config.poll_jitter_seconds

    def test_register_via_method(self) -> None:
        scheduler = _FakeScheduler()
        policy = SupplyChainBlocklistPolicy()
        policy.register_scheduled_tasks(scheduler)
        assert len(scheduler.callbacks) == 1

    def test_register_is_idempotent(self) -> None:
        scheduler = _FakeScheduler()
        policy = SupplyChainBlocklistPolicy()
        policy.register_scheduled_tasks(scheduler)
        policy.register_scheduled_tasks(scheduler)
        assert len(scheduler.registrations) == 1


# =============================================================================
# Streaming shape — MANDATORY regression tests (the class of bug that
# killed #536 and nearly killed #540 v1)
# =============================================================================


class TestStreamingShape:
    """Verify the policy respects the Anthropic streaming protocol.

    - ``content_block_start`` indices must be strictly monotonic across the
      full emitted stream.
    - The count of emitted ``content_block_start`` events must equal the
      count of upstream ``content_block_start`` events.
    - Flagged tool_use blocks keep their ORIGINAL index after substitution.
    - Assertions name specific index values — never just "an event exists".
    """

    @pytest.mark.asyncio
    async def test_flagged_tool_use_preserves_block_index(self) -> None:
        policy = _make_policy([_entry()])
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install litellm==1.6.8"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        starts = [e for e in out if isinstance(e, RawContentBlockStartEvent)]
        assert len(starts) == 1
        assert starts[0].index == 0

    @pytest.mark.asyncio
    async def test_flagged_tool_use_preserves_block_count(self) -> None:
        policy = _make_policy([_entry()])
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install litellm==1.6.8"}'),
            _block_stop(0),
        ]
        upstream_start_count = sum(1 for e in events if isinstance(e, RawContentBlockStartEvent))
        out = await _run_stream(policy, events, ctx)
        emitted_start_count = sum(1 for e in out if isinstance(e, RawContentBlockStartEvent))
        assert emitted_start_count == upstream_start_count == 1

    @pytest.mark.asyncio
    async def test_two_flagged_tool_uses_in_one_response(self) -> None:
        policy = _make_policy(
            [
                _entry(name="litellm", ecosystem=ECOSYSTEM_PYPI, cve="CVE-A", fixed="1.6.9"),
                _entry(
                    name="axios",
                    ecosystem=ECOSYSTEM_NPM,
                    cve="CVE-B",
                    introduced="1.6.8",
                    fixed=None,
                    last_affected="1.6.8",
                ),
            ]
        )
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0, tool_id="t0"),
            _input_delta(0, '{"command": "pip install litellm==1.6.8"}'),
            _block_stop(0),
            _text_start(1),
            _text_delta(1, "ok"),
            _block_stop(1),
            _tool_start(2, tool_id="t2"),
            _input_delta(2, '{"command": "npm install axios@1.6.8"}'),
            _block_stop(2),
        ]
        out = await _run_stream(policy, events, ctx)
        starts = [e for e in out if isinstance(e, RawContentBlockStartEvent)]
        # One start per upstream block, preserving indices [0, 1, 2].
        assert [s.index for s in starts] == [0, 1, 2]
        # Both tool_use blocks have their command rewritten.
        deltas = [e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta)]
        assert len(deltas) == 2
        for d in deltas:
            partial = d.delta.partial_json  # type: ignore[union-attr]
            parsed = json.loads(partial)
            assert parsed["command"].startswith("sh -c")

    @pytest.mark.asyncio
    async def test_monotonic_block_start_across_stream(self) -> None:
        policy = _make_policy([_entry()])
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _text_start(0),
            _text_delta(0, "thinking..."),
            _block_stop(0),
            _tool_start(1, tool_id="flagged"),
            _input_delta(1, '{"command": "pip install litellm==1.6.8"}'),
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
    async def test_flagged_tool_use_rewrites_command_field(self) -> None:
        policy = _make_policy([_entry()])
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install litellm==1.6.8"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        deltas = [e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta)]
        assert len(deltas) == 1
        partial = deltas[0].delta.partial_json  # type: ignore[union-attr]
        assert "sh -c" in partial
        assert "LUTHIEN BLOCKED" in partial
        parsed = json.loads(partial)
        assert parsed["command"].startswith("sh -c")


# =============================================================================
# Streaming basics — passthrough, buffering, orphan flush
# =============================================================================


class TestStreamingBasics:
    @pytest.mark.asyncio
    async def test_passthrough_text_block(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        event = _text_start(0)
        out = await policy.on_anthropic_stream_event(event, ctx)
        assert out == [event]

    @pytest.mark.asyncio
    async def test_non_bash_tool_passthrough(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        event = _tool_start(0, name="Read")
        out = await policy.on_anthropic_stream_event(event, ctx)
        assert out == [event]

    @pytest.mark.asyncio
    async def test_buffers_until_stop(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        out_start = await policy.on_anthropic_stream_event(_tool_start(0), ctx)
        assert out_start == []
        out_delta = await policy.on_anthropic_stream_event(
            _input_delta(0, '{"command": "pip install safe==1.0"}'),
            ctx,
        )
        assert out_delta == []
        out_stop = await policy.on_anthropic_stream_event(_block_stop(0), ctx)
        types = [type(e).__name__ for e in out_stop]
        assert types == [
            "RawContentBlockStartEvent",
            "RawContentBlockDeltaEvent",
            "RawContentBlockStopEvent",
        ]

    @pytest.mark.asyncio
    async def test_unflagged_command_preserved(self) -> None:
        policy = _make_policy([_entry()])
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install requests==2.31.0"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        delta = next(e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta))
        parsed = json.loads(delta.delta.partial_json)  # type: ignore[union-attr]
        assert parsed["command"] == "pip install requests==2.31.0"

    @pytest.mark.asyncio
    async def test_stream_complete_flushes_orphan(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        await policy.on_anthropic_stream_event(_tool_start(0), ctx)
        await policy.on_anthropic_stream_event(_input_delta(0, '{"command": "echo hi"}'), ctx)
        flushed = await policy.on_anthropic_stream_complete(ctx)
        types = [type(e).__name__ for e in flushed]
        assert types == [
            "RawContentBlockStartEvent",
            "RawContentBlockDeltaEvent",
            "RawContentBlockStopEvent",
        ]

    @pytest.mark.asyncio
    async def test_stream_complete_empty(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        out = await policy.on_anthropic_stream_complete(ctx)
        assert out == []

    @pytest.mark.asyncio
    async def test_partial_json_chunks_accumulate(self) -> None:
        policy = _make_policy([_entry()])
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"comma'),
            _input_delta(0, 'nd": "pip install '),
            _input_delta(0, 'litellm==1.6.8"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        delta = next(e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta))
        parsed = json.loads(delta.delta.partial_json)  # type: ignore[union-attr]
        assert parsed["command"].startswith("sh -c")

    @pytest.mark.asyncio
    async def test_streaming_policy_complete_pops_state(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        await policy.on_anthropic_stream_event(_tool_start(0), ctx)
        await policy.on_anthropic_streaming_policy_complete(ctx)
        # Subsequent calls see a fresh state.
        out = await policy.on_anthropic_stream_event(_block_stop(0), ctx)
        # Index 0 is no longer buffered → passthrough.
        assert len(out) == 1


class TestDefensiveFlush:
    """``_handle_block_delta`` must flush buffered state on unexpected deltas."""

    @pytest.mark.asyncio
    async def test_text_delta_at_buffered_tool_use_index_flushes(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        await policy.on_anthropic_stream_event(_tool_start(0), ctx)
        # Hand-construct an unexpected delta: a TextDelta arriving at the
        # buffered tool_use index. The policy must emit the buffered start
        # (so downstream has a matched start/stop pair) plus the unexpected
        # delta itself, then let subsequent events at index 0 flow through.
        unexpected = RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=TextDelta(type="text_delta", text="surprise"),
        )
        out = await policy.on_anthropic_stream_event(unexpected, ctx)
        types = [type(e).__name__ for e in out]
        # Start + input_json_delta (coalesced) + the unexpected text_delta.
        assert types[0] == "RawContentBlockStartEvent"
        assert any(isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta) for e in out)
        assert any(isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta) for e in out)
        # After flush, a stop event at index 0 passes through unchanged.
        stop_out = await policy.on_anthropic_stream_event(_block_stop(0), ctx)
        assert len(stop_out) == 1


# =============================================================================
# Substitution path — primary lookup + substring backstop + no-op
# =============================================================================


class TestSubstitution:
    @pytest.mark.asyncio
    async def test_no_blocklist_no_substitution(self) -> None:
        policy = _make_policy()  # empty index
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"command": "pip install litellm==1.6.8"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        delta = next(e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta))
        parsed = json.loads(delta.delta.partial_json)  # type: ignore[union-attr]
        assert parsed["command"] == "pip install litellm==1.6.8"

    @pytest.mark.asyncio
    async def test_substring_backstop_fires_on_echo_command(self) -> None:
        policy = _make_policy(
            [_entry(name="axios", ecosystem=ECOSYSTEM_NPM, introduced="1.6.8", fixed=None, last_affected="1.6.8")]
        )
        ctx = _make_context()
        events: list[MessageStreamEvent] = [
            _tool_start(0),
            _input_delta(0, '{"command": "echo axios@1.6.8 is bad"}'),
            _block_stop(0),
        ]
        out = await _run_stream(policy, events, ctx)
        delta = next(e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta))
        parsed = json.loads(delta.delta.partial_json)  # type: ignore[union-attr]
        assert parsed["command"].startswith("sh -c")
        assert "LUTHIEN BLOCKED" in parsed["command"]

    @pytest.mark.asyncio
    async def test_non_streaming_response_substitution(self) -> None:
        policy = _make_policy([_entry()])
        ctx = _make_context()
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install litellm==1.6.8"},
                }
            ]
        }
        out = await policy.on_anthropic_response(response, ctx)  # type: ignore[arg-type]
        block = out["content"][0]
        assert block["input"]["command"].startswith("sh -c")  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_non_streaming_passthrough_when_no_match(self) -> None:
        policy = _make_policy()
        ctx = _make_context()
        response: dict[str, Any] = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pip install safe==1.0.0"},
                }
            ]
        }
        out = await policy.on_anthropic_response(response, ctx)  # type: ignore[arg-type]
        assert out["content"][0]["input"]["command"] == "pip install safe==1.0.0"  # type: ignore[index]


# =============================================================================
# Background poller — one tick with a fake OSV client + real in-memory DB
# =============================================================================


class _FakeOSVClient:
    def __init__(self, results: dict[str, list[OSVFetchResult]] | None = None) -> None:
        self._results = results or {}
        self.calls: list[tuple[str, datetime | None]] = []
        self.raise_for: set[str] = set()

    async def fetch_recent(
        self,
        ecosystem: str,
        since: datetime | None,
        min_severity: str,
        limit: int,
    ) -> OSVFetchResult:
        self.calls.append((ecosystem, since))
        if ecosystem in self.raise_for:
            raise RuntimeError(f"simulated OSV failure for {ecosystem}")
        queue = self._results.get(ecosystem, [])
        if not queue:
            return OSVFetchResult(ecosystem=ecosystem, entries=[], latest_published_at=None)
        return queue.pop(0)


async def _build_pool_with_schema() -> db.DatabasePool:
    pool = db.DatabasePool("sqlite://:memory:")
    backing = await pool.get_pool()
    await backing.execute(
        """
        CREATE TABLE supply_chain_blocklist (
            ecosystem      TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            cve_id         TEXT NOT NULL,
            affected_range TEXT NOT NULL,
            severity       TEXT NOT NULL,
            published_at   TEXT NOT NULL,
            fetched_at     TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ecosystem, canonical_name, cve_id, affected_range)
        )
        """
    )
    await backing.execute(
        """
        CREATE TABLE supply_chain_blocklist_cursor (
            ecosystem     TEXT PRIMARY KEY,
            last_seen_at  TEXT NOT NULL,
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    return pool


class TestBackgroundPoller:
    @pytest.mark.asyncio
    async def test_poll_once_ingests_and_indexes(self) -> None:
        from luthien_proxy.policies.supply_chain_blocklist_db import BlocklistRow

        fake_osv = _FakeOSVClient(
            results={
                ECOSYSTEM_PYPI: [
                    OSVFetchResult(
                        ecosystem=ECOSYSTEM_PYPI,
                        entries=[
                            BlocklistRow(
                                ecosystem=ECOSYSTEM_PYPI,
                                canonical_name="litellm",
                                cve_id="CVE-X",
                                affected_range='{"introduced":null,"fixed":"1.6.9","last_affected":null}',
                                severity="CRITICAL",
                                published_at=datetime(2026, 4, 1, tzinfo=UTC),
                            )
                        ],
                        latest_published_at=datetime(2026, 4, 1, tzinfo=UTC),
                    )
                ]
            }
        )
        pool = await _build_pool_with_schema()
        try:
            policy = SupplyChainBlocklistPolicy(
                db_pool=await pool.get_pool(),
                osv_client=fake_osv,  # type: ignore[arg-type]
            )
            await policy.poll_once()
            assert policy.index_size == 1
            # Request-time hit against the freshly-loaded entry.
            ctx = _make_context()
            out = await _run_stream(
                policy,
                [
                    _tool_start(0),
                    _input_delta(0, '{"command": "pip install litellm==1.6.8"}'),
                    _block_stop(0),
                ],
                ctx,
            )
            delta = next(
                e for e in out if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, InputJSONDelta)
            )
            parsed = json.loads(delta.delta.partial_json)  # type: ignore[union-attr]
            assert parsed["command"].startswith("sh -c")
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_poll_once_second_tick_does_not_duplicate(self) -> None:
        from luthien_proxy.policies.supply_chain_blocklist_db import BlocklistRow

        first = OSVFetchResult(
            ecosystem=ECOSYSTEM_PYPI,
            entries=[
                BlocklistRow(
                    ecosystem=ECOSYSTEM_PYPI,
                    canonical_name="litellm",
                    cve_id="CVE-X",
                    affected_range='{"introduced":null,"fixed":"1.6.9","last_affected":null}',
                    severity="CRITICAL",
                    published_at=datetime(2026, 4, 1, tzinfo=UTC),
                )
            ],
            latest_published_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        # Second tick: OSV returns the same row again (as it would if the
        # cursor filter is naive). Our upsert must keep this idempotent.
        second = OSVFetchResult(
            ecosystem=ECOSYSTEM_PYPI,
            entries=list(first.entries),
            latest_published_at=first.latest_published_at,
        )
        fake_osv = _FakeOSVClient(results={ECOSYSTEM_PYPI: [first, second]})
        pool = await _build_pool_with_schema()
        try:
            policy = SupplyChainBlocklistPolicy(
                db_pool=await pool.get_pool(),
                osv_client=fake_osv,  # type: ignore[arg-type]
            )
            await policy.poll_once()
            await policy.poll_once()
            assert policy.index_size == 1
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_poll_once_logs_and_continues_on_osv_failure(self) -> None:
        fake_osv = _FakeOSVClient()
        fake_osv.raise_for = {ECOSYSTEM_PYPI}
        pool = await _build_pool_with_schema()
        try:
            policy = SupplyChainBlocklistPolicy(
                db_pool=await pool.get_pool(),
                osv_client=fake_osv,  # type: ignore[arg-type]
            )
            # Must not raise.
            await policy.poll_once()
            assert policy.index_size == 0
            # Both ecosystems were still attempted.
            called_ecosystems = [c[0] for c in fake_osv.calls]
            assert ECOSYSTEM_PYPI in called_ecosystems
            assert ECOSYSTEM_NPM in called_ecosystems
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_load_initial_state_from_db(self) -> None:
        from luthien_proxy.policies.supply_chain_blocklist_db import BlocklistRow, upsert_entries

        pool = await _build_pool_with_schema()
        try:
            await upsert_entries(
                await pool.get_pool(),
                [
                    BlocklistRow(
                        ecosystem=ECOSYSTEM_PYPI,
                        canonical_name="litellm",
                        cve_id="CVE-SEED",
                        affected_range='{"introduced":null,"fixed":"1.6.9","last_affected":null}',
                        severity="CRITICAL",
                        published_at=datetime(2026, 4, 1, tzinfo=UTC),
                    )
                ],
            )
            policy = SupplyChainBlocklistPolicy(db_pool=await pool.get_pool())
            assert policy.index_size == 0  # Not loaded until explicit call.
            await policy.load_initial_state()
            assert policy.index_size == 1
        finally:
            await pool.close()
