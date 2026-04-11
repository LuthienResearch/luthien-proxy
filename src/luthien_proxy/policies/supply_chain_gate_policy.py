"""SupplyChainGatePolicy — command substitution for vulnerable package installs.

Best-effort supply-chain gate for cooperative LLMs at the Luthien proxy layer.
This policy intercepts bash tool_use calls in Anthropic responses, regex-detects
package install commands, queries OSV for known vulnerabilities, and rewrites
the tool_use's ``command`` field in place to fail loudly with a descriptive
error when a package matches the configured severity threshold. The cooperative
LLM then sees the failed command in the next turn's tool_result and relays the
CVE information to the user through its normal error-reporting path.

This is NOT a security boundary. It does not resist adversarial obfuscation
(``sh -c "$(base64 -d ...)"``, ``eval``, writing scripts and sourcing them,
etc.). A motivated adversarial LLM can trivially bypass it. For hardened
supply-chain defense, run OSV-Scanner inside the execution sandbox at install
time.

The intervention shape is **command substitution**, not content injection.
Flagged tool_use blocks keep their original stream index so indices remain
monotonic and no untrusted OSV text reaches the LLM.

Scope: v3 does not scan incoming tool_result content (e.g., output of
``pip freeze``, ``npm ls``, ``cat package.json``) for already-installed
compromised versions. v2 (PR #536) had such a scanner; v3 drops it because:

(a) the install-time gate catches all future installs;
(b) the awareness-only signal added prompt-injection surface and complex
    injection-shape problems for marginal value;
(c) operators who need to detect already-installed compromised versions
    should run OSV-Scanner against their lockfiles out-of-band.

This is a deliberate scope decision, not an oversight. See
``dev/context/decisions.md`` entry dated 2026-04-10 for the rationale.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    ToolUseBlock,
)

from luthien_proxy.policies.supply_chain_gate_utils import (
    InstallMatch,
    OSVClient,
    PackageCheckResult,
    PackageRef,
    Severity,
    SupplyChainGateConfig,
    VulnInfo,
    build_blocked_command,
    build_lockfile_substitute,
    extract_install_commands,
    redact_credentials,
)
from luthien_proxy.policy_core import AnthropicHookPolicy, BasePolicy
from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicPolicyEmission

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        AnthropicToolUseBlock,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.utils.policy_cache import PolicyCache

logger = logging.getLogger(__name__)


@dataclass
class _BufferedBashToolUse:
    """A tool_use block we're buffering during streaming."""

    id: str
    name: str
    input_json: str = ""


@dataclass
class _GateState:
    """Per-request streaming state."""

    buffered_tool_uses: dict[int, _BufferedBashToolUse] = field(default_factory=dict)


class SupplyChainGatePolicy(BasePolicy, AnthropicHookPolicy):
    """Rewrite bash installs of known-vulnerable packages as failing commands."""

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name."""
        return "SupplyChainGate"

    def __init__(
        self,
        config: SupplyChainGateConfig | dict | None = None,
        osv_client: OSVClient | None = None,
    ) -> None:
        """Initialize with optional config and an injectable OSV client (tests)."""
        self.config = self._init_config(config, SupplyChainGateConfig)
        self._threshold: Severity = self.config.severity_threshold_enum
        self._bash_tool_names: frozenset[str] = frozenset(self.config.bash_tool_names)
        self._blocklist: frozenset[str] = frozenset(self.config.explicit_blocklist)
        self._osv = osv_client or OSVClient(
            api_url=self.config.osv_api_url,
            timeout_seconds=self.config.osv_timeout_seconds,
        )
        logger.info(
            "SupplyChainGatePolicy initialized: threshold=%s, fail_mode=%s, block_lockfile=%s",
            self._threshold.label,
            self.config.osv_fail_mode,
            self.config.block_lockfile_installs,
        )

    def _state(self, context: "PolicyContext") -> _GateState:
        return context.get_request_state(self, _GateState, _GateState)

    def _buffered(self, context: "PolicyContext") -> dict[int, _BufferedBashToolUse]:
        return self._state(context).buffered_tool_uses

    def _cache(self, context: "PolicyContext") -> "PolicyCache | None":
        if not context.has_policy_cache:
            return None
        try:
            return context.policy_cache(self.short_policy_name)
        except RuntimeError:
            return None

    async def _lookup_vulns(self, package: PackageRef, context: "PolicyContext") -> tuple[list[VulnInfo], str | None]:
        """Get vulns for ``package``, using the policy cache when available."""
        cache = self._cache(context)
        key = package.cache_key()
        if cache is not None:
            try:
                cached = await cache.get(key)
            except Exception as exc:
                logger.warning("policy_cache get failed for %s: %s", key, exc)
                cached = None
            if cached is not None:
                if cached.get("error") is not None:
                    return [], str(cached["error"])
                return [VulnInfo.from_dict(v) for v in cached.get("vulns", [])], None
        try:
            vulns = await self._osv.query(package)
        except Exception as exc:
            logger.warning("OSV query failed for %s: %s", key, exc)
            if cache is not None and self.config.negative_cache_ttl_seconds > 0:
                try:
                    await cache.put(
                        key,
                        {"error": str(exc), "vulns": []},
                        ttl_seconds=self.config.negative_cache_ttl_seconds,
                    )
                except Exception as cache_exc:
                    logger.warning("policy_cache put (error) failed for %s: %s", key, cache_exc)
            return [], str(exc)
        if cache is not None and self.config.cache_ttl_seconds > 0:
            try:
                await cache.put(
                    key,
                    {"vulns": [v.to_dict() for v in vulns]},
                    ttl_seconds=self.config.cache_ttl_seconds,
                )
            except Exception as exc:
                logger.warning("policy_cache put failed for %s: %s", key, exc)
        return vulns, None

    async def _check_packages(self, packages: list[PackageRef], context: "PolicyContext") -> list[PackageCheckResult]:
        """Look up every package concurrently, capped by the semaphore."""
        if not packages:
            return []
        semaphore = asyncio.Semaphore(self.config.max_concurrent_lookups)

        async def bounded(pkg: PackageRef) -> tuple[list[VulnInfo], str | None]:
            async with semaphore:
                return await self._lookup_vulns(pkg, context)

        lookups = await asyncio.gather(*(bounded(p) for p in packages))
        return [
            PackageCheckResult(package=pkg, vulns=vulns, error=error, blocklisted=self._is_blocklisted(pkg))
            for pkg, (vulns, error) in zip(packages, lookups)
        ]

    def _is_blocklisted(self, package: PackageRef) -> bool:
        key = package.blocklist_key()
        return key is not None and key in self._blocklist

    def _should_substitute(self, results: list[PackageCheckResult]) -> bool:
        """Decide whether to substitute the command based on check results."""
        if any(r.triggers(self._threshold) for r in results):
            return True
        if self.config.osv_fail_mode == "block":
            return any(r.error is not None for r in results)
        return False

    async def _rewrite_if_needed(self, command: str, context: "PolicyContext") -> str | None:
        """Return the substitute command if the gate fires, else ``None``."""
        if not command:
            return None
        matches = extract_install_commands(command)
        if not matches:
            return None
        lockfile_hit = self._maybe_lockfile_substitute(command, matches, context)
        if lockfile_hit is not None:
            return lockfile_hit
        packages = _dedupe_packages([p for m in matches for p in m.packages])
        if not packages:
            return None
        results = await self._check_packages(packages, context)
        if any(r.error is not None for r in results) and self.config.osv_fail_mode == "warn":
            errored = [r.package.name for r in results if r.error is not None]
            logger.warning(
                "OSV unreachable for %d package(s); passing through per fail_mode=warn: %s",
                len(errored),
                ", ".join(errored),
            )
        if not self._should_substitute(results):
            return None
        context.record_event(
            "policy.supply_chain_gate.substituted",
            {
                "summary": f"Supply chain gate substituted install command ({len(results)} pkg)",
                "command": redact_credentials(command),
                "threshold": self._threshold.label,
                "packages": [
                    {
                        "ecosystem": r.package.ecosystem,
                        "name": r.package.name,
                        "version": r.package.version,
                        "max_severity": r.max_severity.label,
                        "blocklisted": r.blocklisted,
                        "error": r.error,
                    }
                    for r in results
                ],
            },
        )
        return build_blocked_command(command, results, self._threshold)

    def _maybe_lockfile_substitute(
        self, command: str, matches: list[InstallMatch], context: "PolicyContext"
    ) -> str | None:
        """Return a lockfile-review substitute if any match is a lockfile install."""
        if not self.config.block_lockfile_installs:
            return None
        for match in matches:
            if match.is_lockfile:
                context.record_event(
                    "policy.supply_chain_gate.lockfile_held",
                    {
                        "summary": "Lockfile install held by supply-chain gate",
                        "command": redact_credentials(command),
                        "manager": match.manager,
                        "verb": match.verb,
                        "requirement_file": match.requirement_file,
                        "constraint_file": match.constraint_file,
                    },
                )
                return build_lockfile_substitute(match, command)
        return None

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Drop per-request state when streaming finishes."""
        context.pop_request_state(self, _GateState)

    async def on_anthropic_stream_complete(self, context: "PolicyContext") -> list[AnthropicPolicyEmission]:
        """Flush any tool_use blocks still buffered when the upstream stream ends."""
        buffered_map = self._buffered(context)
        if not buffered_map:
            return []
        emissions: list[AnthropicPolicyEmission] = []
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
        """Buffer Bash tool_use blocks; rewrite the command at stop time."""
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
        # Unexpected delta type at a buffered tool_use index. We already
        # swallowed the content_block_start; emitting the delta alone would
        # leave downstream with an orphaned delta. Flush the buffered block
        # in passthrough mode (unmodified start + any accumulated input JSON)
        # and then emit the unexpected delta. Future events at this index
        # pass through untouched.
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
        substitute = await self._rewrite_if_needed(command or "", context)
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
                    substitute = await self._rewrite_if_needed(command or "", context)
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


def _extract_bash_command(tool_input: object) -> str | None:
    """Pull the ``command`` string out of a Bash tool_use input dict."""
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    return None


def _extract_command_from_json(raw_json: str) -> str | None:
    """Best-effort parse of a buffered tool_use input JSON."""
    if not raw_json:
        return None
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    return _extract_bash_command(parsed)


def _rewrite_command_in_input_json(raw_json: str, new_command: str) -> str:
    """Return a fresh input-json string with ``command`` replaced."""
    try:
        parsed = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    parsed["command"] = new_command
    return json.dumps(parsed, ensure_ascii=False)


def _dedupe_packages(packages: list[PackageRef]) -> list[PackageRef]:
    """De-dupe by (ecosystem, name, version), preserving order."""
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[PackageRef] = []
    for pkg in packages:
        key = (pkg.ecosystem, pkg.name, pkg.version)
        if key not in seen:
            seen.add(key)
            unique.append(pkg)
    return unique


def _reemit_tool_use_events(buffered: _BufferedBashToolUse, index: int, input_json: str) -> list[MessageStreamEvent]:
    """Rebuild the (start, delta) events for a buffered tool_use block.

    Partial-json chunk boundaries are lost; we coalesce into a single delta.
    """
    tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
    start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_use_block)
    delta_event = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=input_json or "{}"),
    )
    return [cast(MessageStreamEvent, start_event), cast(MessageStreamEvent, delta_event)]


__all__ = ["SupplyChainGatePolicy"]
