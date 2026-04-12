"""Tests for SupplyChainFeedPolicy — streaming shape, substitution, lifecycle.

Mandatory test categories from OBJECTIVE.md:
3. Streaming-shape correctness (5 mandatory tests asserting specific event.index)
5. _handle_block_delta defensive flush test
6. Background task tests
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
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

from luthien_proxy.policies.supply_chain_feed_db import create_schema, upsert_entries
from luthien_proxy.policies.supply_chain_feed_policy import SupplyChainFeedPolicy
from luthien_proxy.policies.supply_chain_feed_utils import (
    VulnEntry,
    build_blocklist_index,
    build_substrate_strings,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.utils.db import DatabasePool

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "osv"


# =====================================================================
# Helpers
# =====================================================================


def _make_policy_with_blocklist(entries: list[VulnEntry]) -> SupplyChainFeedPolicy:
    """Create a policy with a pre-populated blocklist (no DB needed)."""
    policy = SupplyChainFeedPolicy()
    policy._index = build_blocklist_index(entries)
    policy._substrate_strings = build_substrate_strings(policy._index)
    return policy


def _ctx() -> PolicyContext:
    return PolicyContext.for_testing()


def _tool_use_start(index: int, tool_id: str = "tool_1", name: str = "bash") -> RawContentBlockStartEvent:
    block = ToolUseBlock(type="tool_use", id=tool_id, name=name, input={})
    return RawContentBlockStartEvent(type="content_block_start", index=index, content_block=block)


def _input_delta(index: int, partial: str) -> RawContentBlockDeltaEvent:
    delta = InputJSONDelta(type="input_json_delta", partial_json=partial)
    return RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=delta)


def _block_stop(index: int) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


def _text_start(index: int) -> RawContentBlockStartEvent:
    block = TextBlock(type="text", text="hello")
    return RawContentBlockStartEvent(type="content_block_start", index=index, content_block=block)


def _text_delta(index: int, text: str) -> RawContentBlockDeltaEvent:
    delta = TextDelta(type="text_delta", text=text)
    return RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=delta)


BLOCKLIST_ENTRIES = [
    VulnEntry("PyPI", "calibreweb", "0.6.17", "GHSA-8ppf", "CRITICAL", None, None),
    VulnEntry("npm", "axios", "1.6.8", "CVE-2024-39338", "CRITICAL", None, None),
]


# =====================================================================
# Category 3: Streaming-shape correctness (5 mandatory tests)
# =====================================================================


class TestStreamingShapeCorrectness:
    """Each test asserts specific event.index values with explicit equality."""

    @pytest.mark.asyncio
    async def test_flagged_tool_use_preserves_block_index(self):
        """Flagged tool_use should emit events at the SAME index as the original."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        # Stream: start(1) -> delta(1, command) -> stop(1)
        events: list[MessageStreamEvent] = []
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(1)), ctx))
        events.extend(
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _input_delta(1, '{"command": "pip install calibreweb==0.6.17"}')), ctx
            )
        )
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(1)), ctx))

        # Should get 3 events: start, delta, stop — all at index 1
        assert len(events) == 3
        for event in events:
            assert event.index == 1  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_flagged_tool_use_preserves_block_count(self):
        """Flagged tool_use emits exactly 3 events: start, delta, stop."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        events: list[MessageStreamEvent] = []
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(0)), ctx))
        events.extend(
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _input_delta(0, '{"command": "npm install axios@1.6.8"}')), ctx
            )
        )
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx))

        assert len(events) == 3
        # start, delta, stop
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[2], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_two_flagged_tool_uses_in_one_response(self):
        """Two independent flagged tool_uses maintain their respective indices."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        all_events: list[MessageStreamEvent] = []

        # First tool_use at index 0
        all_events.extend(
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(0, tool_id="t1")), ctx)
        )
        all_events.extend(
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _input_delta(0, '{"command": "pip install calibreweb==0.6.17"}')), ctx
            )
        )
        all_events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx))

        # Second tool_use at index 2
        all_events.extend(
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(2, tool_id="t2")), ctx)
        )
        all_events.extend(
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _input_delta(2, '{"command": "npm install axios@1.6.8"}')), ctx
            )
        )
        all_events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(2)), ctx))

        assert len(all_events) == 6

        # First 3 events at index 0
        for event in all_events[:3]:
            assert event.index == 0  # type: ignore[attr-defined]

        # Last 3 events at index 2
        for event in all_events[3:]:
            assert event.index == 2  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_monotonic_block_start_across_stream(self):
        """Block start indices across the entire stream must be monotonically non-decreasing."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        all_events: list[MessageStreamEvent] = []

        # text at 0
        all_events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _text_start(0)), ctx))
        all_events.extend(
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _text_delta(0, "hello")), ctx)
        )
        all_events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx))

        # flagged bash at 1
        all_events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(1)), ctx))
        all_events.extend(
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _input_delta(1, '{"command": "pip install calibreweb==0.6.17"}')), ctx
            )
        )
        all_events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(1)), ctx))

        # Extract content_block_start indices
        start_indices = [e.index for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert start_indices == [0, 1]  # monotonically increasing

    @pytest.mark.asyncio
    async def test_flagged_tool_use_rewrites_command_field(self):
        """The rewritten tool_use must contain the substitute command."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        events: list[MessageStreamEvent] = []
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(0)), ctx))
        events.extend(
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _input_delta(0, '{"command": "pip install calibreweb==0.6.17"}')), ctx
            )
        )
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx))

        # The delta event should contain the substitute
        delta_event = events[1]
        assert isinstance(delta_event, RawContentBlockDeltaEvent)
        assert isinstance(delta_event.delta, InputJSONDelta)
        rewritten = json.loads(delta_event.delta.partial_json)
        assert "LUTHIEN BLOCKED" in rewritten["command"]
        assert "exit 42" in rewritten["command"]
        assert "GHSA-8ppf" in rewritten["command"]


# =====================================================================
# Category 5: _handle_block_delta defensive flush test
# =====================================================================


class TestDefensiveFlush:
    @pytest.mark.asyncio
    async def test_unexpected_delta_type_flushes_buffer(self):
        """An unexpected delta type at a buffered index must flush before passing through."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        # Start buffering a bash tool_use
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(0)), ctx)

        # Send a text_delta at the same index (unexpected)
        text_delta = _text_delta(0, "unexpected")
        events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta), ctx)

        # Should get: flushed start + flushed delta + the unexpected event
        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert events[0].index == 0
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert events[1].index == 0
        # The unexpected event is passed through
        assert isinstance(events[2], RawContentBlockDeltaEvent)
        assert events[2].index == 0


# =====================================================================
# Unflagged tool_use passthrough
# =====================================================================


class TestPassthrough:
    @pytest.mark.asyncio
    async def test_safe_command_passes_through(self):
        """A bash command not in the blocklist passes through unchanged."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        events: list[MessageStreamEvent] = []
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_use_start(0)), ctx))
        events.extend(
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _input_delta(0, '{"command": "pip install requests==2.31.0"}')), ctx
            )
        )
        events.extend(await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx))

        # Should reconstruct original: start, delta, stop
        assert len(events) == 3
        delta_event = events[1]
        assert isinstance(delta_event, RawContentBlockDeltaEvent)
        assert isinstance(delta_event.delta, InputJSONDelta)
        assert "requests" in delta_event.delta.partial_json

    @pytest.mark.asyncio
    async def test_non_bash_tool_use_passes_through(self):
        """Non-bash tool_use blocks are not buffered."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        # A tool_use with name != "bash"
        block = ToolUseBlock(type="tool_use", id="t1", name="python", input={})
        event = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=block)
        events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, event), ctx)
        # Should pass through immediately
        assert len(events) == 1
        assert events[0] is event


# =====================================================================
# Non-streaming response test
# =====================================================================


class TestNonStreamingResponse:
    @pytest.mark.asyncio
    async def test_flagged_non_streaming(self):
        """Non-streaming response with flagged bash tool_use is rewritten."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        response = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "bash",
                    "input": {"command": "pip install calibreweb==0.6.17"},
                }
            ],
            "stop_reason": "tool_use",
        }

        result = await policy.on_anthropic_response(response, ctx)  # type: ignore[arg-type]
        block = result["content"][0]
        assert "LUTHIEN BLOCKED" in block["input"]["command"]

    @pytest.mark.asyncio
    async def test_safe_non_streaming(self):
        """Non-streaming response without flagged commands passes through."""
        policy = _make_policy_with_blocklist(BLOCKLIST_ENTRIES)
        ctx = _ctx()

        response = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "bash",
                    "input": {"command": "pip install requests==2.31.0"},
                }
            ],
        }

        result = await policy.on_anthropic_response(response, ctx)  # type: ignore[arg-type]
        assert result is response  # unchanged


# =====================================================================
# Category 6: Background task tests
# =====================================================================


class TestBackgroundTask:
    @pytest.fixture
    async def db_pool(self):
        db = DatabasePool("sqlite://:memory:")
        pool = await db.get_pool()
        await create_schema(pool)
        return pool

    @pytest.mark.asyncio
    async def test_poll_with_fixture_cold_start(self, db_pool):
        """Simulate cold start using pinned fixture, assert DB has right entries."""
        policy = SupplyChainFeedPolicy()
        policy._db_pool = db_pool

        zip_bytes = (FIXTURES / "pypi_sample.zip").read_bytes()

        # Mock httpx.AsyncClient to return our fixture zip
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = zip_bytes
        mock_response.raise_for_status = AsyncMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("luthien_proxy.policies.supply_chain_feed_policy.httpx.AsyncClient", return_value=mock_client):
            await policy._cold_start("PyPI")

        # Verify DB has entries
        rows = await db_pool.fetch("SELECT DISTINCT cve_id FROM supply_chain_feed")
        cve_ids = {str(r["cve_id"]) for r in rows}
        # Only CRITICAL entries from the fixture
        assert "GHSA-8ppf-x4gr-2x7g" in cve_ids
        assert "GHSA-xp7p-3gx7-j6wx" in cve_ids
        # HIGH/MODERATE should not be present
        assert "GHSA-x6xg-3fj2-4pq3" not in cve_ids

    @pytest.mark.asyncio
    async def test_poll_failure_does_not_advance_cursor(self, db_pool):
        """If cold start fails, cursor should remain None."""
        policy = SupplyChainFeedPolicy()
        policy._db_pool = db_pool

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("luthien_proxy.policies.supply_chain_feed_policy.httpx.AsyncClient", return_value=mock_client):
            # Should not raise — failures are caught
            await policy._poll_ecosystem("PyPI")

        from luthien_proxy.policies.supply_chain_feed_db import get_cursor as gc

        cursor = await gc(db_pool, "PyPI")
        assert cursor is None

    @pytest.mark.asyncio
    async def test_no_duplicates_on_second_poll(self, db_pool):
        """Second poll with same data should not create duplicates."""
        policy = SupplyChainFeedPolicy()
        policy._db_pool = db_pool

        zip_bytes = (FIXTURES / "pypi_sample.zip").read_bytes()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = zip_bytes
        mock_response.raise_for_status = AsyncMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("luthien_proxy.policies.supply_chain_feed_policy.httpx.AsyncClient", return_value=mock_client):
            await policy._cold_start("PyPI")
            count1 = len(await db_pool.fetch("SELECT * FROM supply_chain_feed"))
            await policy._cold_start("PyPI")
            count2 = len(await db_pool.fetch("SELECT * FROM supply_chain_feed"))

        assert count1 == count2  # no duplicates


# =====================================================================
# on_policy_loaded lifecycle test
# =====================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_on_policy_loaded_with_db(self):
        """on_policy_loaded wires up db_pool and loads index."""
        db = DatabasePool("sqlite://:memory:")
        pool = await db.get_pool()
        await create_schema(pool)

        # Pre-populate DB
        pub = datetime(2025, 1, 1, tzinfo=timezone.utc)
        await upsert_entries(
            pool,
            [
                ("PyPI", "calibreweb", "0.6.17", "GHSA-8ppf", "CRITICAL", pub, pub),
            ],
        )

        policy = SupplyChainFeedPolicy()

        class FakeContext:
            db_pool = pool
            scheduler = None

        await policy.on_policy_loaded(FakeContext())

        assert ("pypi", "calibreweb", "0.6.17") in policy._index

    @pytest.mark.asyncio
    async def test_on_policy_loaded_no_db(self):
        """on_policy_loaded with no db_pool runs with empty blocklist."""
        policy = SupplyChainFeedPolicy()

        class FakeContext:
            db_pool = None
            scheduler = None

        await policy.on_policy_loaded(FakeContext())
        assert policy._index == {}
