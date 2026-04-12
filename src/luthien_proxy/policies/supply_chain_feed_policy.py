"""Supply chain feed policy — block bash tool_use for known-compromised packages.

Best-effort, cooperative-LLM only. This is NOT a security boundary.
Run OSV-Scanner in CI for lockfile coverage.

The blocklist is built and refreshed by an in-process background task that
pulls OSV's bulk-download GCS feed every few minutes, filters to CRITICAL
advisories, and stores the pre-expanded (ecosystem, name, version) -> cve_id
mapping in a DB-agnostic table. At request time, the policy does an O(1) dict
lookup (plus a literal-substring backstop) and, on a hit, rewrites
tool_use.input.command to an ``sh -c 'exit 42'`` substitute.

Lockfile installs (``npm ci``, ``pip install -r requirements.txt``) are
explicitly out of scope. Use OSV-Scanner in CI for lockfile coverage.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import httpx
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    ToolUseBlock,
)

from luthien_proxy.policies.supply_chain_feed_db import (
    get_cursor,
    load_all_entries,
    set_cursor,
    upsert_entries,
)
from luthien_proxy.policies.supply_chain_feed_utils import (
    SUPPORTED_ECOSYSTEMS,
    VulnEntry,
    build_blocklist_index,
    build_substitute_command,
    build_substrate_strings,
    bulk_zip_url,
    check_blocklist,
    listing_api_url,
    parse_bulk_zip,
    parse_listing_page,
    parse_vuln_json,
)
from luthien_proxy.policy_core import AnthropicHookPolicy, BasePolicy

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.utils.db import PoolProtocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-request streaming state
# ---------------------------------------------------------------------------


@dataclass
class _BufferedToolUse:
    """Accumulated input_json_delta for a single tool_use block."""

    id: str
    name: str
    input_json: str = ""


@dataclass
class _StreamState:
    """Per-request streaming state for buffering bash tool_use blocks."""

    buffered: dict[int, _BufferedToolUse] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class SupplyChainFeedPolicy(BasePolicy, AnthropicHookPolicy):
    """Block bash tool_use commands that install known-compromised package versions.

    See module docstring for the full design and scope.
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "SupplyChainFeed"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize with optional config dict (currently unused)."""
        # In-memory blocklist — populated by on_policy_loaded via _load_index_from_db
        # and refreshed by the background task. Protected by _index_lock.
        self._index: dict[tuple[str, str, str], list[str]] = {}
        self._substrate_strings: frozenset[str] = frozenset()
        self._index_lock = asyncio.Lock()
        self._db_pool: PoolProtocol | None = None

    def freeze_configured_state(self) -> None:
        """Override to skip mutable-container check for runtime state.

        _index, _substrate_strings, and _index_lock are runtime-managed state,
        not config-time mutable containers. They are updated atomically under
        a lock by the background task and read without lock at request time
        (dict reads are atomic in CPython).
        """
        pass

    # ------------------------------------------------------------------
    # Lifecycle hooks (called by PolicyManager after construction)
    # ------------------------------------------------------------------

    async def on_policy_loaded(self, context: Any) -> None:
        """Wire up db_pool and load the initial blocklist from DB.

        Called by PolicyManager.initialize() after construction. The context
        carries gateway services (db_pool, scheduler).
        """
        db_pool = getattr(context, "db_pool", None)
        if db_pool is None:
            logger.warning("SupplyChainFeedPolicy: no db_pool provided, running with empty blocklist")
            return

        from luthien_proxy.utils.db import DatabasePool  # noqa: PLC0415

        if isinstance(db_pool, DatabasePool):
            self._db_pool = await db_pool.get_pool()
        else:
            self._db_pool = db_pool

        await self._load_index_from_db()

        # Register background task if scheduler is available
        scheduler = getattr(context, "scheduler", None)
        if scheduler is not None:
            scheduler.add_task(
                name="supply_chain_feed_poll",
                callback=self._poll_osv,
                interval_seconds=300,
                jitter_seconds=60,
            )

    async def _load_index_from_db(self) -> None:
        """Load the full blocklist from DB into memory."""
        if self._db_pool is None:
            return
        try:
            rows = await load_all_entries(self._db_pool)
            entries = [
                VulnEntry(
                    ecosystem=str(r["ecosystem"]),
                    name=str(r["name"]),
                    version=str(r["version"]),
                    cve_id=str(r["cve_id"]),
                    severity="CRITICAL",
                    published_at=None,
                    modified_at=None,
                )
                for r in rows
            ]
            new_index = build_blocklist_index(entries)
            new_substrates = build_substrate_strings(new_index)
            async with self._index_lock:
                self._index = new_index
                self._substrate_strings = new_substrates
            logger.info("Loaded %d blocklist entries from DB", len(new_index))
        except Exception:
            logger.warning("Failed to load blocklist from DB", exc_info=True)

    # ------------------------------------------------------------------
    # Background task: poll OSV for updates
    # ------------------------------------------------------------------

    async def _poll_osv(self) -> None:
        """Poll OSV for each supported ecosystem. Called by the scheduler."""
        for ecosystem in SUPPORTED_ECOSYSTEMS:
            await self._poll_ecosystem(ecosystem)

    async def _poll_ecosystem(self, ecosystem: str) -> None:
        """Poll a single ecosystem — cold start or incremental.

        On failure: logs WARNING, does not advance cursor, retries next tick.
        """
        if self._db_pool is None:
            return

        try:
            cursor = await get_cursor(self._db_pool, ecosystem)
            if cursor is None:
                await self._cold_start(ecosystem)
            else:
                await self._incremental_update(ecosystem, cursor)

            # Reload index from DB after successful poll
            await self._load_index_from_db()
        except Exception:
            logger.warning("Failed to poll OSV for %s", ecosystem, exc_info=True)

    async def _cold_start(self, ecosystem: str) -> None:
        """Download the bulk zip and populate the DB."""
        url = bulk_zip_url(ecosystem)
        logger.info("Cold start: downloading %s", url)

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=120.0)
            resp.raise_for_status()
            zip_bytes = resp.content

        entries = parse_bulk_zip(zip_bytes)
        if not entries:
            logger.warning("Cold start for %s: no entries parsed from bulk zip", ecosystem)
            return

        db_entries = [
            (e.ecosystem, e.name, e.version, e.cve_id, e.severity, e.published_at, e.modified_at) for e in entries
        ]
        count = await upsert_entries(self._db_pool, db_entries)  # type: ignore[arg-type]
        logger.info("Cold start for %s: upserted %d entries", ecosystem, count)

        # Set cursor to max modified_at
        max_modified = max((e.modified_at for e in entries if e.modified_at is not None), default=None)
        if max_modified is not None:
            await set_cursor(self._db_pool, ecosystem, max_modified)  # type: ignore[arg-type]

    async def _incremental_update(self, ecosystem: str, cursor_dt: Any) -> None:
        """Fetch individual vuln JSONs newer than the cursor via listing API."""
        from datetime import datetime, timezone  # noqa: PLC0415

        if isinstance(cursor_dt, str):
            cursor_dt = datetime.fromisoformat(cursor_dt)
        if cursor_dt.tzinfo is None:
            cursor_dt = cursor_dt.replace(tzinfo=timezone.utc)

        all_entries: list[VulnEntry] = []
        max_modified = cursor_dt
        page_token: str | None = None

        async with httpx.AsyncClient() as client:
            while True:
                url = listing_api_url(ecosystem, page_token=page_token)
                resp = await client.get(url, timeout=30.0)
                resp.raise_for_status()
                data = resp.json()

                items, next_token = parse_listing_page(data)
                new_items = [item for item in items if item.updated > cursor_dt]

                for item in new_items:
                    vuln_url = f"https://storage.googleapis.com/osv-vulnerabilities/{item.name}"
                    vuln_resp = await client.get(vuln_url, timeout=15.0)
                    if vuln_resp.status_code != 200:
                        continue
                    try:
                        vuln_data = vuln_resp.json()
                        parsed = parse_vuln_json(vuln_data)
                        all_entries.extend(parsed)
                        for entry in parsed:
                            if entry.modified_at and entry.modified_at > max_modified:
                                max_modified = entry.modified_at
                    except Exception:
                        logger.warning("Failed to parse %s", item.name, exc_info=True)

                if next_token is None:
                    break
                page_token = next_token

        if all_entries:
            db_entries = [
                (e.ecosystem, e.name, e.version, e.cve_id, e.severity, e.published_at, e.modified_at)
                for e in all_entries
            ]
            count = await upsert_entries(self._db_pool, db_entries)  # type: ignore[arg-type]
            logger.info("Incremental update for %s: upserted %d entries", ecosystem, count)

        if max_modified > cursor_dt:
            await set_cursor(self._db_pool, ecosystem, max_modified)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Anthropic hook implementations
    # ------------------------------------------------------------------

    def _stream_state(self, context: "PolicyContext") -> _StreamState:
        return context.get_request_state(self, _StreamState, _StreamState)

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Passthrough — no request modifications."""
        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Check non-streaming responses for bash tool_use blocks."""
        content = response.get("content", [])
        if not content:
            return response

        modified = False
        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "bash":
                input_data = block.get("input", {})
                raw_cmd = input_data.get("command", "") if isinstance(input_data, dict) else ""
                command = str(raw_cmd) if raw_cmd else ""
                if command:
                    hit = check_blocklist(command, self._index, self._substrate_strings)
                    if hit:
                        name, version, cve_ids = hit
                        substitute = build_substitute_command(name, version, cve_ids)
                        new_block = dict(block)
                        new_input = dict(input_data) if isinstance(input_data, dict) else {"command": ""}
                        new_input["command"] = substitute
                        new_block["input"] = new_input
                        new_content.append(new_block)
                        modified = True
                        logger.info("Blocked %s==%s in non-streaming response", name, version)
                        continue
            new_content.append(block)

        if modified:
            result = dict(response)
            result["content"] = new_content
            return cast("AnthropicResponse", result)
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer bash tool_use events; check and substitute on block_stop."""
        if isinstance(event, RawContentBlockStartEvent):
            return self._handle_block_start(event, context)
        elif isinstance(event, RawContentBlockDeltaEvent):
            return self._handle_block_delta(event, context)
        elif isinstance(event, RawContentBlockStopEvent):
            return self._handle_block_stop(event, context)
        return [event]

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------

    def _handle_block_start(
        self, event: RawContentBlockStartEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer bash tool_use starts; pass everything else through."""
        block = event.content_block
        if isinstance(block, ToolUseBlock) and block.name == "bash":
            state = self._stream_state(context)
            state.buffered[event.index] = _BufferedToolUse(id=block.id, name=block.name)
            return []
        return [event]

    def _handle_block_delta(
        self, event: RawContentBlockDeltaEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Accumulate input_json_delta for buffered blocks."""
        state = self._stream_state(context)
        if event.index in state.buffered:
            if isinstance(event.delta, InputJSONDelta):
                state.buffered[event.index].input_json += event.delta.partial_json
                return []
            else:
                # Unexpected delta type at a buffered index — flush before passing through
                buffered = state.buffered.pop(event.index)
                flushed = self._reconstruct_passthrough(buffered, event.index)
                flushed.append(event)
                return [cast(MessageStreamEvent, e) for e in flushed]
        return [event]

    def _handle_block_stop(self, event: RawContentBlockStopEvent, context: "PolicyContext") -> list[MessageStreamEvent]:
        """On block stop, check the buffered command and decide: substitute or pass through."""
        state = self._stream_state(context)
        if event.index not in state.buffered:
            return [cast(MessageStreamEvent, event)]

        buffered = state.buffered.pop(event.index)
        command = self._extract_command(buffered.input_json)

        if command:
            hit = check_blocklist(command, self._index, self._substrate_strings)
            if hit:
                name, version, cve_ids = hit
                substitute = build_substitute_command(name, version, cve_ids)
                logger.info("Blocked %s==%s in stream at index %d", name, version, event.index)
                return self._emit_substitute(buffered, event.index, substitute, event)

        # No hit — reconstruct original events
        events = self._reconstruct_passthrough(buffered, event.index)
        events.append(event)
        return [cast(MessageStreamEvent, e) for e in events]

    def _extract_command(self, input_json: str) -> str:
        """Parse the accumulated input JSON to extract the command string."""
        try:
            data = json.loads(input_json) if input_json else {}
        except json.JSONDecodeError:
            return ""
        if isinstance(data, dict):
            return str(data.get("command", ""))
        return ""

    def _reconstruct_passthrough(self, buffered: _BufferedToolUse, index: int) -> list[Any]:
        """Reconstruct the original events from a buffered tool_use."""
        tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
        start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_use_block)
        json_delta = InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}")
        delta = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=json_delta)
        return [start, delta]

    def _emit_substitute(
        self,
        buffered: _BufferedToolUse,
        index: int,
        substitute_command: str,
        stop_event: RawContentBlockStopEvent,
    ) -> list[MessageStreamEvent]:
        """Emit the rewritten tool_use with the substitute command."""
        # Same block ID, same name, same index — just rewrite the input
        tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
        start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_use_block)
        new_json = json.dumps({"command": substitute_command})
        json_delta = InputJSONDelta(type="input_json_delta", partial_json=new_json)
        delta = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=json_delta)
        return [
            cast(MessageStreamEvent, start),
            cast(MessageStreamEvent, delta),
            cast(MessageStreamEvent, stop_event),
        ]


__all__ = ["SupplyChainFeedPolicy"]
