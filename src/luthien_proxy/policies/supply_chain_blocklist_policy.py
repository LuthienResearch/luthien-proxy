"""SupplyChainBlocklistPolicy — best-effort blocklist for compromised package installs.

This policy blocks bash ``tool_use`` install commands for known-compromised
package versions. The blocklist is built and refreshed by an in-process
background task that polls OSV every few minutes for newly-published CRITICAL
CVEs. Request-time checks are a tiny in-memory lookup with PEP 440 / semver
range matching plus a literal-substring backstop. On a hit, the policy
rewrites the bash ``tool_use.input.command`` field in place to a
``sh -c '... LUTHIEN BLOCKED ... exit 42'`` substitute — the cooperative LLM
then sees a failed command in the next turn's ``tool_result`` and relays the
CVE information to the user via its normal error-reporting path.

Explicit non-properties of this policy:

- **Not a security boundary against adversarial obfuscation.** A motivated
  adversarial LLM can trivially bypass it (``sh -c "$(base64 -d ...)"``,
  ``eval``, writing scripts and sourcing them). Cooperative LLMs only.
- **Does not cover lockfile installs** (``npm ci``, ``pip install -r``, ``yarn
  install --frozen-lockfile``). Run OSV-Scanner in CI for lockfile coverage.
- **Minutes-resolution freshness only.** The gap between CVE publication and
  the next poll tick is the window where compromised installs can slip
  through. The policy's value is catching the gap between publication and
  registry yank, not providing zero-latency detection.

Three prior attempts at this feature (PRs #522, #536, #540) are all closed.
The meta-pattern that killed them was regex-parsing free-form bash strings to
decide whether to make an OSV call, then bolting on layer after layer to
handle edge cases in that parser. This fourth design shifts OSV traffic to a
background task, making the request-time path a literal lookup that does not
need to interpret arbitrary commands — most of the prior edge-case load
dissolves as a result.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    ToolUseBlock,
)

from luthien_proxy.policies.supply_chain_blocklist_db import (
    BlocklistRow,
    get_cursor,
    load_all_entries,
    set_cursor,
    upsert_entries,
)
from luthien_proxy.policies.supply_chain_blocklist_utils import (
    SUPPORTED_ECOSYSTEMS,
    BlocklistEntry,
    BlocklistIndex,
    OSVClient,
    SupplyChainBlocklistConfig,
    build_substitute_command,
    extract_literals,
)
from luthien_proxy.policy_core import AnthropicHookPolicy, BasePolicy

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        AnthropicToolUseBlock,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.utils.db import PoolProtocol

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Scheduler protocol (stub — PR worktree-policy-scheduler will wire the real
# implementation). The policy accepts any object satisfying this shape so
# tests can pass a trivial in-memory scheduler and PR A can swap in the real
# one without touching policy code.
# -----------------------------------------------------------------------------


class SchedulerProtocol(Protocol):
    """The minimal surface area the policy needs from a scheduler."""

    def schedule(
        self,
        name: str,
        interval_seconds: float,
        callback: Callable[[], Awaitable[None]],
        jitter_seconds: float = 0.0,
        run_immediately: bool = False,
    ) -> None:
        """Register a periodic callback."""
        ...


# -----------------------------------------------------------------------------
# Per-request state
# -----------------------------------------------------------------------------


@dataclass
class _BufferedBashToolUse:
    """A tool_use block being buffered across the stream until its stop event."""

    id: str
    name: str
    input_json: str = ""


@dataclass
class _PolicyState:
    """Per-request mutable state stored on ``PolicyContext``."""

    buffered_tool_uses: dict[int, _BufferedBashToolUse] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Policy
# -----------------------------------------------------------------------------


class SupplyChainBlocklistPolicy(BasePolicy, AnthropicHookPolicy):
    """Substitute bash install commands that match a known-compromised entry."""

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name used in emitted events."""
        return "SupplyChainBlocklist"

    def __init__(
        self,
        config: SupplyChainBlocklistConfig | dict | None = None,
        *,
        db_pool: "PoolProtocol | None" = None,
        osv_client: OSVClient | None = None,
        scheduler: SchedulerProtocol | None = None,
    ) -> None:
        """Create a stateless policy instance.

        Args:
            config: Policy configuration (or raw dict from YAML).
            db_pool: Shared DB pool (DB-agnostic via ``PoolProtocol``). When
                unset, the policy runs with an empty blocklist — PR
                ``worktree-policy-scheduler`` will wire the real pool through
                ``register_scheduled_tasks``.
            osv_client: Injectable OSV client for tests. Defaults to the
                real HTTP-backed client.
            scheduler: Optional scheduler to register the background task
                against immediately (test convenience; production uses
                ``register_scheduled_tasks``).
        """
        self.config = self._init_config(config, SupplyChainBlocklistConfig)
        self._bash_tool_names: frozenset[str] = frozenset(self.config.bash_tool_names)
        self._db_pool: "PoolProtocol | None" = db_pool
        self._osv = osv_client or OSVClient(
            api_url=self.config.osv_api_url,
            timeout_seconds=self.config.osv_timeout_seconds,
        )
        # Immutable snapshot-swap: we never mutate the index in place. The
        # background task assembles a new BlocklistIndex from all entries and
        # atomically rebinds ``self._index``. Reads (per-request) see a
        # consistent snapshot at any given instant.
        self._index: BlocklistIndex = BlocklistIndex([])
        self._scheduler_registered = False
        if scheduler is not None:
            self.register_scheduled_tasks(scheduler)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register_scheduled_tasks(self, scheduler: SchedulerProtocol) -> None:
        """Register the blocklist poller with ``scheduler``.

        Called by the policy loader once PR ``worktree-policy-scheduler`` is
        merged. The scheduler is responsible for calling ``poll_once`` at
        ``poll_interval_seconds`` with ``poll_jitter_seconds`` of jitter and
        running it once immediately on startup.
        """
        if self._scheduler_registered:
            return
        scheduler.schedule(
            name=f"{self.short_policy_name}.poll",
            interval_seconds=self.config.poll_interval_seconds,
            callback=self.poll_once,
            jitter_seconds=self.config.poll_jitter_seconds,
            run_immediately=True,
        )
        self._scheduler_registered = True

    async def load_initial_state(self, db_pool: "PoolProtocol | None" = None) -> None:
        """Populate the in-memory index from the DB at startup.

        Tests and the (future) scheduler-backed lifecycle can call this
        explicitly. No-op when no pool is available.
        """
        pool = db_pool or self._db_pool
        if pool is None:
            return
        try:
            rows = await load_all_entries(pool)
        except Exception as exc:
            logger.warning("Failed to load supply-chain blocklist from DB: %s", exc)
            return
        self._index = BlocklistIndex(BlocklistEntry.from_row(r) for r in rows)
        logger.info("Supply-chain blocklist loaded: %d entries", len(self._index))

    async def poll_once(self) -> None:
        """Run one tick of the background poller.

        Fetches new advisories for each supported ecosystem, upserts them
        into the DB, advances the per-ecosystem cursor, and rebuilds the
        in-memory index. Failures for one ecosystem do not block the others
        and are logged, not raised — the scheduler will retry on the next
        tick.
        """
        pool = self._db_pool
        if pool is None:
            logger.debug("SupplyChainBlocklistPolicy.poll_once: no DB pool, skipping")
            return
        for ecosystem in SUPPORTED_ECOSYSTEMS:
            try:
                await self._poll_ecosystem(pool, ecosystem)
            except Exception as exc:
                logger.warning("OSV poll failed for ecosystem=%s: %s", ecosystem, exc)
        await self._refresh_index(pool)

    async def _poll_ecosystem(self, pool: "PoolProtocol", ecosystem: str) -> None:
        since = await get_cursor(pool, ecosystem)
        result = await self._osv.fetch_recent(
            ecosystem=ecosystem,
            since=since,
            min_severity=self.config.min_severity,
            limit=self.config.max_entries_per_tick,
        )
        if result.entries:
            await upsert_entries(pool, result.entries)
        watermark = _pick_watermark(since, result.entries, result.latest_published_at)
        if watermark is not None:
            await set_cursor(pool, ecosystem, watermark)

    async def _refresh_index(self, pool: "PoolProtocol") -> None:
        try:
            rows = await load_all_entries(pool)
        except Exception as exc:
            logger.warning("Failed to refresh supply-chain blocklist index: %s", exc)
            return
        self._index = BlocklistIndex(BlocklistEntry.from_row(r) for r in rows)

    # ------------------------------------------------------------------
    # Per-request hooks
    # ------------------------------------------------------------------

    def _state(self, context: "PolicyContext") -> _PolicyState:
        return context.get_request_state(self, _PolicyState, _PolicyState)

    def _buffered(self, context: "PolicyContext") -> dict[int, _BufferedBashToolUse]:
        return self._state(context).buffered_tool_uses

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Drop per-request state when the streaming pipeline finishes."""
        context.pop_request_state(self, _PolicyState)

    async def on_anthropic_stream_complete(self, context: "PolicyContext") -> list[MessageStreamEvent]:
        """Flush any tool_use blocks still buffered when the upstream stream ends."""
        buffered_map = self._buffered(context)
        if not buffered_map:
            return []
        emissions: list[MessageStreamEvent] = []
        for index, buffered in sorted(buffered_map.items()):
            logger.warning(
                "Stream ended with tool_use still buffered (index=%d, id=%s); emitting as-is",
                index,
                buffered.id,
            )
            emissions.extend(_reemit_tool_use_events(buffered, index, buffered.input_json or "{}"))
            emissions.append(
                cast(
                    MessageStreamEvent,
                    RawContentBlockStopEvent(type="content_block_stop", index=index),
                )
            )
        buffered_map.clear()
        return emissions

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer bash tool_use blocks; rewrite the command at stop time."""
        if isinstance(event, RawContentBlockStartEvent):
            return self._handle_block_start(event, context)
        if isinstance(event, RawContentBlockDeltaEvent):
            return self._handle_block_delta(event, context)
        if isinstance(event, RawContentBlockStopEvent):
            return await self._handle_block_stop(event, context)
        return [event]

    def _handle_block_start(
        self, event: RawContentBlockStartEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        block = event.content_block
        if isinstance(block, ToolUseBlock) and block.name in self._bash_tool_names:
            self._buffered(context)[event.index] = _BufferedBashToolUse(id=block.id, name=block.name)
            return []
        return [event]

    def _handle_block_delta(
        self, event: RawContentBlockDeltaEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        buffered = self._buffered(context)
        if event.index not in buffered:
            return [event]
        if isinstance(event.delta, InputJSONDelta):
            buffered[event.index].input_json += event.delta.partial_json
            return []
        # Defensive flush: an unexpected delta type arrived at an index where
        # we already swallowed the content_block_start. Emitting the delta
        # alone would leave the downstream consumer with an orphaned delta.
        # Release the buffered block first (unmodified start + any accumulated
        # input JSON) so downstream sees a well-formed sequence, then pass
        # through the unexpected delta.
        logger.warning(
            "Unexpected delta type %s at buffered index %d; flushing buffered block and releasing",
            type(event.delta).__name__,
            event.index,
        )
        flushed = buffered.pop(event.index)
        emissions = _reemit_tool_use_events(flushed, event.index, flushed.input_json or "{}")
        emissions.append(event)
        return emissions

    async def _handle_block_stop(
        self, event: RawContentBlockStopEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        buffered_map = self._buffered(context)
        if event.index not in buffered_map:
            return [cast(MessageStreamEvent, event)]
        buffered = buffered_map.pop(event.index)
        command = _extract_command_from_json(buffered.input_json)
        substitute = self._maybe_substitute(command or "", context)
        input_json = (
            _rewrite_command_in_input_json(buffered.input_json, substitute)
            if substitute is not None
            else (buffered.input_json or "{}")
        )
        reemit = _reemit_tool_use_events(buffered, event.index, input_json)
        reemit.append(cast(MessageStreamEvent, event))
        return reemit

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Mutate ``content[]`` in place for any flagged bash tool_use blocks."""
        content = response.get("content", [])
        if not content:
            return response
        new_content: list[AnthropicContentBlock] = []
        modified = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use = cast("AnthropicToolUseBlock", block)
                if tool_use.get("name") in self._bash_tool_names:
                    command = _extract_bash_command(tool_use.get("input", {}))
                    substitute = self._maybe_substitute(command or "", context)
                    if substitute is not None:
                        new_block = dict(tool_use)
                        new_input = dict(tool_use.get("input") or {})
                        new_input["command"] = substitute
                        new_block["input"] = cast(Any, new_input)
                        new_content.append(cast("AnthropicContentBlock", new_block))
                        modified = True
                        continue
            new_content.append(block)
        if not modified:
            return response
        modified_response = dict(response)
        modified_response["content"] = new_content
        return cast("AnthropicResponse", modified_response)

    # ------------------------------------------------------------------
    # Check path
    # ------------------------------------------------------------------

    def _maybe_substitute(self, command: str, context: "PolicyContext") -> str | None:
        """Return a substitute command if ``command`` matches a blocklist entry."""
        if not command:
            return None
        index = self._index
        if len(index) == 0:
            return None
        # Primary path: extract literals via the loose regex, run each against
        # the range matcher.
        for extracted in extract_literals(command):
            entry = index.lookup(extracted)
            if entry is not None:
                context.record_event(
                    "policy.supply_chain_blocklist.substituted",
                    {
                        "summary": (
                            f"Supply chain blocklist substituted install of {extracted.name}@{extracted.version}"
                        ),
                        "ecosystem": extracted.ecosystem,
                        "package": extracted.name,
                        "version": extracted.version,
                        "cve_id": entry.cve_id,
                        "severity": entry.severity,
                        "matched_via": "range",
                    },
                )
                return build_substitute_command(command, entry)
        # Backstop: exact literal substring scan for advisories that pin a
        # single version (catches line continuations, exotic spacing, and
        # wrappers without needing adversarial parsing).
        backstop = index.substring_backstop(command)
        if backstop is not None:
            context.record_event(
                "policy.supply_chain_blocklist.substituted",
                {
                    "summary": (
                        f"Supply chain blocklist substituted install matching "
                        f"substring literal for {backstop.canonical_name}"
                    ),
                    "ecosystem": backstop.ecosystem,
                    "package": backstop.canonical_name,
                    "cve_id": backstop.cve_id,
                    "severity": backstop.severity,
                    "matched_via": "substring",
                },
            )
            return build_substitute_command(command, backstop)
        return None

    # ------------------------------------------------------------------
    # Test-facing hooks
    # ------------------------------------------------------------------

    def set_index_for_testing(self, entries: list[BlocklistEntry]) -> None:
        """Inject a fixed in-memory index for tests.

        Tests that want to exercise the request-time path without spinning
        up a DB pool use this hook. It is intentionally public (mutable
        rebind of the private snapshot field is the only state change).
        """
        self._index = BlocklistIndex(entries)

    @property
    def index_size(self) -> int:
        """Return the number of entries in the current in-memory index."""
        return len(self._index)


# =============================================================================
# Helpers
# =============================================================================


def _extract_bash_command(tool_input: object) -> str | None:
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    return None


def _extract_command_from_json(raw_json: str) -> str | None:
    if not raw_json:
        return None
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    return _extract_bash_command(parsed)


def _rewrite_command_in_input_json(raw_json: str, new_command: str) -> str:
    try:
        parsed = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    parsed["command"] = new_command
    return json.dumps(parsed, ensure_ascii=False)


def _reemit_tool_use_events(buffered: _BufferedBashToolUse, index: int, input_json: str) -> list[MessageStreamEvent]:
    """Rebuild the (start, delta) events for a buffered tool_use block.

    Partial-json chunk boundaries are lost; we coalesce into one delta.
    """
    tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
    start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_use_block)
    delta_event = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=input_json or "{}"),
    )
    return [cast(MessageStreamEvent, start_event), cast(MessageStreamEvent, delta_event)]


def _pick_watermark(
    previous: datetime | None,
    entries: list[BlocklistRow],
    latest_published_at: datetime | None,
) -> datetime | None:
    """Pick the next cursor watermark.

    Prefer the parser's explicit ``latest_published_at`` (already filtered by
    ``since``); if not present, use the max ``published_at`` across the
    ingested entries; otherwise keep the previous cursor.
    """
    if latest_published_at is not None:
        if previous is None or latest_published_at > previous:
            return latest_published_at
        return previous
    if entries:
        entries_max = max(e.published_at for e in entries)
        if previous is None or entries_max > previous:
            return entries_max
    return previous


# TODO(PR-policy-scheduler): production wiring for the background poll task.
# Today the policy is only driven by tests (which call ``poll_once`` directly
# or pass a ``scheduler=`` arg) and therefore runs with an empty blocklist in
# any production deployment that has not been wired to the scheduler loader.
# When PR ``worktree-policy-scheduler`` merges, the policy manager will
# invoke ``register_scheduled_tasks`` after instantiating the policy and the
# real DB pool; at that point the blocklist will begin populating on schedule.


__all__ = ["SchedulerProtocol", "SupplyChainBlocklistPolicy"]
