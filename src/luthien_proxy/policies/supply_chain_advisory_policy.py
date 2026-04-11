"""SupplyChainAdvisoryPolicy — warn on known-vulnerable package installs.

This is a **best-effort CVE advisory policy for cooperative LLMs**. It is
NOT a security boundary and is NOT designed to resist an adversarial LLM
obfuscating install commands (e.g. ``sh -c``, chain operators, ``eval``,
base64 decoding, or non-standard package managers). Its purpose is to
warn a cooperative LLM — and through it, the user — when a
known-compromised package version (CVSS severity >= HIGH by default) is
about to be installed or is already present in tool output. For hardened
supply-chain defence, run OSV-Scanner inside the sandbox at install time.

Threat model
------------
The real-world cases this policy addresses are recent incidents (litellm,
axios) where a CVE was public but the poisoned version was still
installable because the registry had not yanked it yet. The LLM is
cooperative — it is not trying to sneak anything past the policy — it's
just as blind to the CVE as the user. A loose regex over the outgoing
command is enough to catch the common case, and false negatives from
exotic command shapes are acceptable: they're simply outside scope.

Hooks
-----
- ``on_anthropic_request``: scans ``tool_result`` blocks in the latest
  user message for already-installed vulnerable versions (``pip freeze``,
  ``npm ls``, ``cat package.json`` output) and prepends an advisory to
  the system prompt.
- ``on_anthropic_stream_event``: buffers ``Bash`` tool_use blocks until
  the input JSON is complete, runs the advisory scan, and emits an
  advisory text block alongside the original tool_use when flagged.
- ``on_anthropic_response`` (non-streaming): same as the stream path but
  applied to an already-complete response.

OSV lookup results are cached via ``context.policy_cache`` when a cache
factory is available, falling back to no-cache otherwise.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

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

from luthien_proxy.policies.supply_chain_advisory_utils import (
    OSVClient,
    PackageCheckResult,
    PackageRef,
    Severity,
    SupplyChainAdvisoryConfig,
    VulnInfo,
    extract_install_packages,
    extract_tool_result_packages,
    format_advisory_message,
    redact_credentials,
)
from luthien_proxy.policy_core import AnthropicHookPolicy, BasePolicy
from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicPolicyEmission

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicMessage,
        AnthropicRequest,
        AnthropicResponse,
        AnthropicSystemBlock,
        AnthropicToolResultBlock,
        AnthropicToolUseBlock,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.utils.policy_cache import PolicyCache

logger = logging.getLogger(__name__)


@dataclass
class _BufferedBashToolUse:
    """A tool_use block we've started buffering during streaming."""

    id: str
    name: str
    input_json: str = ""


@dataclass
class _AdvisoryState:
    """Per-request streaming state."""

    buffered_tool_uses: dict[int, _BufferedBashToolUse] = field(default_factory=dict)


class SupplyChainAdvisoryPolicy(BasePolicy, AnthropicHookPolicy):
    """Warn when a Bash command mentions a known-vulnerable package."""

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "SupplyChainAdvisory"

    def __init__(
        self,
        config: SupplyChainAdvisoryConfig | dict | None = None,
        osv_client: OSVClient | None = None,
    ) -> None:
        """Initialize with optional config and an injectable OSV client (tests)."""
        self.config = self._init_config(config, SupplyChainAdvisoryConfig)
        self._threshold: Severity = self.config.severity_threshold_enum
        self._bash_tool_names: frozenset[str] = frozenset(self.config.bash_tool_names)
        self._osv = osv_client or OSVClient(
            api_url=self.config.osv_api_url,
            timeout_seconds=self.config.osv_timeout_seconds,
        )
        logger.info(
            "SupplyChainAdvisoryPolicy initialized: threshold=%s, warn_on_osv_error=%s",
            self._threshold.label,
            self.config.warn_on_osv_error,
        )

    # ========================================================================
    # State / cache helpers
    # ========================================================================

    def _state(self, context: "PolicyContext") -> _AdvisoryState:
        return context.get_request_state(self, _AdvisoryState, _AdvisoryState)

    def _buffered(self, context: "PolicyContext") -> dict[int, _BufferedBashToolUse]:
        return self._state(context).buffered_tool_uses

    def _cache(self, context: "PolicyContext") -> "PolicyCache | None":
        if not context.has_policy_cache:
            return None
        try:
            return context.policy_cache(self.short_policy_name)
        except RuntimeError:
            return None

    async def _lookup_vulns(
        self,
        package: PackageRef,
        context: "PolicyContext",
    ) -> tuple[list[VulnInfo], str | None]:
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
                cached_error = cached.get("error")
                if cached_error is not None:
                    return [], str(cached_error)
                return [VulnInfo.from_dict(v) for v in cached.get("vulns", [])], None

        try:
            vulns = await self._osv.query(package)
        except Exception as exc:
            logger.warning("OSV query failed for %s: %s", key, exc)
            if cache is not None and self.config.error_cache_ttl_seconds > 0:
                try:
                    await cache.put(
                        key,
                        {"error": str(exc), "vulns": []},
                        ttl_seconds=self.config.error_cache_ttl_seconds,
                    )
                except Exception as cache_exc:
                    logger.warning("policy_cache put (error) failed for %s: %s", key, cache_exc)
            return [], str(exc)

        if cache is not None:
            try:
                await cache.put(
                    key,
                    {"vulns": [v.to_dict() for v in vulns]},
                    ttl_seconds=self.config.cache_ttl_seconds,
                )
            except Exception as exc:
                logger.warning("policy_cache put failed for %s: %s", key, exc)

        return vulns, None

    async def _check_packages(
        self,
        packages: list[PackageRef],
        context: "PolicyContext",
    ) -> list[PackageCheckResult]:
        """Look up every package concurrently, capped by the semaphore."""
        if not packages:
            return []

        semaphore = asyncio.Semaphore(self.config.max_concurrent_lookups)

        async def bounded(pkg: PackageRef) -> tuple[list[VulnInfo], str | None]:
            async with semaphore:
                return await self._lookup_vulns(pkg, context)

        # _lookup_vulns swallows its own exceptions and returns them via the
        # error field, so gather() without return_exceptions is safe.
        lookups = await asyncio.gather(*(bounded(p) for p in packages))
        return [
            PackageCheckResult(package=pkg, vulns=vulns, error=error) for pkg, (vulns, error) in zip(packages, lookups)
        ]

    def _advisory_results(self, results: list[PackageCheckResult]) -> list[PackageCheckResult]:
        """Return the subset of results to surface in an advisory."""
        flagged = [r for r in results if r.has_advisory(self._threshold)]
        if self.config.warn_on_osv_error:
            flagged += [r for r in results if r.error and r not in flagged]
        return flagged

    # ========================================================================
    # Streaming
    # ========================================================================

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Drop per-request state when streaming finishes (multi-policy path)."""
        context.pop_request_state(self, _AdvisoryState)

    async def on_anthropic_stream_complete(self, context: "PolicyContext") -> list[AnthropicPolicyEmission]:
        """Flush any tool_use blocks still buffered when the upstream stream ends.

        If the stream was truncated mid tool_use we never saw the stop event,
        so the buffered data never got re-emitted. Emit the raw tool_use
        events as we have them (no advisory scan is possible without the
        complete input JSON) so the client still sees a coherent response.
        """
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
            emissions.extend(_reemit_tool_use(buffered, index))
        buffered_map.clear()
        return emissions

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer Bash tool_use blocks until complete and inject an advisory if needed."""
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
            return []  # suppress until we've seen the complete input JSON
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
        # Non-input-json delta at a buffered index shouldn't happen under
        # the Anthropic protocol; pass it through so we don't silently drop
        # anything the client might need.
        logger.warning(
            "Unexpected delta type %s at buffered index %d; passing through",
            type(event.delta).__name__,
            event.index,
        )
        return [event]

    async def _handle_block_stop(
        self, event: RawContentBlockStopEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        buffered_map = self._buffered(context)
        if event.index not in buffered_map:
            return [cast(MessageStreamEvent, event)]
        buffered = buffered_map.pop(event.index)

        command = _extract_command_from_json(buffered.input_json)
        advisory_text = await self._advisory_for_command(command, context)

        reemit = _reemit_tool_use(buffered, event.index)
        reemit.append(cast(MessageStreamEvent, event))

        if advisory_text is None:
            return reemit

        # Inject a text block BEFORE the tool_use at a lower index so the
        # LLM sees the advisory before the tool call in the client's view.
        # The re-emitted tool_use keeps its original index so downstream
        # tool_result correlation still works.
        advisory_events = _build_advisory_text_events(advisory_text, index=event.index + 1000)
        return [*advisory_events, *reemit]

    # ========================================================================
    # Non-streaming response
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Scan content blocks of a non-streaming response and inject advisory if needed."""
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
                    advisory = await self._advisory_for_command(command, context)
                    if advisory is not None:
                        new_content.append({"type": "text", "text": advisory})
                        modified = True
            new_content.append(block)

        if not modified:
            return response
        modified_response = dict(response)
        modified_response["content"] = new_content
        return cast("AnthropicResponse", modified_response)

    async def _advisory_for_command(self, command: str | None, context: "PolicyContext") -> str | None:
        """Check a Bash command for supply-chain advisories and render a warning."""
        if not command:
            return None
        packages = extract_install_packages(command)
        if not packages:
            return None

        unique = _dedupe_packages(packages)
        results = await self._check_packages(unique, context)
        advisory_subset = self._advisory_results(results)
        if not advisory_subset:
            return None

        context.record_event(
            "policy.supply_chain_advisory.flagged",
            {
                "summary": f"Supply chain advisory flagged {len(advisory_subset)} package(s)",
                "command": redact_credentials(command),
                "packages": [
                    {
                        "ecosystem": r.package.ecosystem,
                        "name": r.package.name,
                        "version": r.package.version,
                        "max_severity": r.max_severity.label,
                        "error": r.error,
                    }
                    for r in advisory_subset
                ],
            },
        )
        return format_advisory_message(advisory_subset, self._threshold, command=command)

    # ========================================================================
    # Incoming request: scan tool_results from prior commands
    # ========================================================================

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Inject a system-prompt advisory when prior tool_results mention vulnerable versions."""
        messages: list[AnthropicMessage] = list(request.get("messages") or [])
        if not messages:
            return request

        last_message = messages[-1]
        if last_message.get("role") != "user":
            return request

        tool_result_texts = _collect_tool_result_texts(last_message)
        if not tool_result_texts:
            return request

        all_packages: list[PackageRef] = []
        for text in tool_result_texts:
            # Install commands could appear in tool results (shell transcripts
            # pasted back by the user) and fresh "currently installed" output.
            all_packages.extend(extract_install_packages(text))
            all_packages.extend(extract_tool_result_packages(text))
        if not all_packages:
            return request

        unique = _dedupe_packages(all_packages)
        results = await self._check_packages(unique, context)
        advisory_subset = self._advisory_results(results)
        if not advisory_subset:
            return request

        warning = format_advisory_message(advisory_subset, self._threshold)
        modified = dict(request)
        modified["system"] = _prepend_system_warning(request.get("system"), warning)

        context.record_event(
            "policy.supply_chain_advisory.incoming_warning",
            {
                "summary": f"Detected {len(advisory_subset)} vulnerable package(s) in tool output",
                "packages": [
                    {
                        "ecosystem": r.package.ecosystem,
                        "name": r.package.name,
                        "version": r.package.version,
                        "max_severity": r.max_severity.label,
                    }
                    for r in advisory_subset
                ],
            },
        )
        return cast("AnthropicRequest", modified)


# =============================================================================
# Module-level helpers
# =============================================================================


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


def _dedupe_packages(packages: list[PackageRef]) -> list[PackageRef]:
    """De-dupe by (ecosystem, name, version), preserving order."""
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[PackageRef] = []
    for pkg in packages:
        key = (pkg.ecosystem, pkg.name, pkg.version)
        if key in seen:
            continue
        seen.add(key)
        unique.append(pkg)
    return unique


def _collect_tool_result_texts(message: "AnthropicMessage") -> list[str]:
    """Return the text contents of every tool_result block in a user message."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tool_result = cast("AnthropicToolResultBlock", block)
        texts.extend(_tool_result_text(tool_result))
    return texts


def _tool_result_text(tool_result: "AnthropicToolResultBlock") -> list[str]:
    """Extract text fragments from a tool_result block.

    Anthropic tool_result content may be a string or a list of content blocks.
    We collect only text blocks; non-text blocks (images, etc.) can't
    plausibly contain version info for this best-effort scan.
    """
    raw = tool_result.get("content")
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                out.append(text)
    return out


def _prepend_system_warning(
    existing: "str | list[AnthropicSystemBlock] | None",
    warning: str,
) -> "str | list[AnthropicSystemBlock]":
    """Prepend a warning string to the request's ``system`` field."""
    if existing is None:
        return warning
    if isinstance(existing, str):
        return f"{warning}\n\n{existing}" if existing else warning
    warning_block: AnthropicSystemBlock = {"type": "text", "text": warning}
    return [warning_block, *existing]


def _reemit_tool_use(buffered: _BufferedBashToolUse, index: int) -> list[MessageStreamEvent]:
    """Rebuild the (start, delta) events for a buffered tool_use block.

    Upstream may have sent multiple partial-JSON deltas; we coalesce them
    into a single delta because we only buffered the accumulated JSON, not
    the original chunk boundaries. Clients tolerate this.
    """
    tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
    start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_use_block)
    delta_event = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}"),
    )
    return [
        cast(MessageStreamEvent, start_event),
        cast(MessageStreamEvent, delta_event),
    ]


def _build_advisory_text_events(text: str, index: int) -> list[MessageStreamEvent]:
    """Build (start, delta, stop) events for an injected text advisory block."""
    text_block = TextBlock(type="text", text="")
    start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=text_block)
    delta = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=text),
    )
    stop = RawContentBlockStopEvent(type="content_block_stop", index=index)
    return [
        cast(MessageStreamEvent, start),
        cast(MessageStreamEvent, delta),
        cast(MessageStreamEvent, stop),
    ]


__all__ = ["SupplyChainAdvisoryPolicy"]
