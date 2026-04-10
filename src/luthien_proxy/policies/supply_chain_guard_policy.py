"""SupplyChainGuardPolicy — block installs of vulnerable packages.

The policy intercepts ``Bash`` tool calls that look like package install
commands (pip, npm, cargo, go, gem, composer) and checks each extracted
package against the OSV.dev vulnerability database. Packages that expose
known high/critical severity vulnerabilities are blocked.

The policy runs in **both directions**:

- **Outgoing** (``on_anthropic_stream_event`` / ``on_anthropic_response``):
  Intercepts ``tool_use`` blocks with install commands before they reach
  the client. Replaces the ``tool_use`` with a text block explaining the
  CVEs when blocking vulnerabilities are found.

- **Incoming** (``on_anthropic_request``): Scans the last user message
  for ``tool_result`` blocks produced by an earlier install command. If
  those installs already introduced vulnerable packages, a warning is
  prepended to the system prompt so the LLM knows to remediate. We do
  not block the request — the install already happened.

OSV lookup results are cached via ``context.policy_cache("SupplyChainGuard")``
when a database is configured, falling back to a process-local dict for
environments without a DB (tests, dockerless dev).
"""

from __future__ import annotations

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

from luthien_proxy.policies.supply_chain_guard_utils import (
    OSVClient,
    PackageCheckResult,
    PackageRef,
    Severity,
    SupplyChainGuardConfig,
    VulnInfo,
    analyze_command,
    filter_blocking,
    format_blocked_message,
    format_hard_block_message,
    format_incoming_warning,
    is_allowlisted,
    parse_install_commands,
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


# Tool name that Claude Code uses for shell execution.
BASH_TOOL_NAME = "Bash"


@dataclass
class _BufferedBashToolUse:
    id: str
    name: str
    input_json: str = ""


@dataclass
class _SupplyChainGuardState:
    """Per-request streaming state for SupplyChainGuardPolicy."""

    buffered_tool_uses: dict[int, _BufferedBashToolUse] = field(default_factory=dict)


class SupplyChainGuardPolicy(BasePolicy, AnthropicHookPolicy):
    """Blocks vulnerable package installs and warns on already-installed vulns."""

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "SupplyChainGuard"

    def __init__(
        self,
        config: SupplyChainGuardConfig | dict | None = None,
        osv_client: OSVClient | None = None,
    ) -> None:
        """Initialize with optional config and an injectable OSV client (for tests)."""
        self.config = self._init_config(config, SupplyChainGuardConfig)
        self._allowlist: frozenset[str] = frozenset(self.config.allowlist)
        self._threshold: Severity = self.config.severity_threshold_enum
        self._osv = osv_client or OSVClient(
            api_url=self.config.osv_api_url,
            timeout_seconds=self.config.osv_timeout_seconds,
        )
        logger.info(
            "SupplyChainGuardPolicy initialized: threshold=%s, allowlist_size=%d, fail_closed=%s",
            self._threshold.label,
            len(self._allowlist),
            self.config.fail_closed,
        )

    # ========================================================================
    # State helpers
    # ========================================================================

    def _state(self, context: "PolicyContext") -> _SupplyChainGuardState:
        return context.get_request_state(self, _SupplyChainGuardState, _SupplyChainGuardState)

    def _buffered(self, context: "PolicyContext") -> dict[int, _BufferedBashToolUse]:
        return self._state(context).buffered_tool_uses

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Drop per-request state when streaming finishes."""
        context.pop_request_state(self, _SupplyChainGuardState)

    async def on_anthropic_stream_complete(self, context: "PolicyContext") -> list[AnthropicPolicyEmission]:
        """Flush any tool_use blocks still buffered when the upstream stream ends.

        Normally ``_handle_content_block_stop`` drains the buffer for each block,
        but if the upstream stream aborts mid tool_use (client disconnect, upstream
        error) those blocks would otherwise be silently dropped. Fail-safe: we
        emit a text block explaining that evaluation was truncated so the caller
        sees that the install was not approved.
        """
        state = context.get_request_state(self, _SupplyChainGuardState, _SupplyChainGuardState)
        buffered_map = state.buffered_tool_uses
        if not buffered_map:
            return []

        emissions: list[AnthropicPolicyEmission] = []
        for index, buffered in sorted(buffered_map.items()):
            logger.warning(
                "Stream ended with tool_use still buffered (index=%d, id=%s); emitting fallback block",
                index,
                buffered.id,
            )
            context.record_event(
                "policy.supply_chain_guard.stream_truncated",
                {
                    "summary": "Supply chain guard dropped an unevaluated Bash tool_use on stream abort",
                    "tool_use_id": buffered.id,
                    "index": index,
                },
            )
            fallback_text = (
                "⛔ Supply chain guard could not finish evaluating this Bash tool call "
                "before the stream ended. The install was NOT executed — please retry."
            )
            emissions.append(
                cast(
                    MessageStreamEvent,
                    RawContentBlockStartEvent(
                        type="content_block_start",
                        index=index,
                        content_block=TextBlock(type="text", text=""),
                    ),
                )
            )
            emissions.append(
                cast(
                    MessageStreamEvent,
                    RawContentBlockDeltaEvent(
                        type="content_block_delta",
                        index=index,
                        delta=TextDelta(type="text_delta", text=fallback_text),
                    ),
                )
            )
            emissions.append(
                cast(
                    MessageStreamEvent,
                    RawContentBlockStopEvent(type="content_block_stop", index=index),
                )
            )
        buffered_map.clear()
        return emissions

    # ========================================================================
    # Cache helpers
    # ========================================================================

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
        """Get vulns for ``package``, using the policy cache when available.

        Returns ``(vulns, error)`` — ``error`` is set when the OSV query failed.
        """
        cache = self._cache(context)
        key = package.cache_key()

        if cache is not None:
            try:
                cached = await cache.get(key)
            except Exception as exc:  # DB hiccup shouldn't break the policy
                logger.warning("policy_cache get failed for %s: %s", key, exc)
                cached = None
            if cached is not None:
                return [VulnInfo.from_dict(v) for v in cached.get("vulns", [])], None

        try:
            vulns = await self._osv.query(package)
        except Exception as exc:
            logger.warning("OSV query failed for %s: %s", key, exc)
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
        """Look up every package, honouring the allowlist."""
        results: list[PackageCheckResult] = []
        for package in packages:
            if is_allowlisted(package, self._allowlist):
                continue
            vulns, error = await self._lookup_vulns(package, context)
            results.append(PackageCheckResult(package=package, vulns=vulns, error=error))
        return results

    def _should_block(self, results: list[PackageCheckResult]) -> bool:
        """Decide whether the overall command should be blocked.

        - Any package with a blocking vuln -> block.
        - If ``fail_closed`` is set, any OSV lookup error -> block.
        """
        for result in results:
            if result.has_blocking(self._threshold):
                return True
            if result.error and self.config.fail_closed:
                return True
        return False

    def _blocking_subset(self, results: list[PackageCheckResult]) -> list[PackageCheckResult]:
        """Return the subset of results to show the user when blocking."""
        subset = filter_blocking(results, self._threshold)
        if self.config.fail_closed:
            subset += [r for r in results if r.error and r not in subset]
        return subset

    # ========================================================================
    # Outgoing: non-streaming response
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Scan content blocks of a non-streaming response and block if needed."""
        content = response.get("content", [])
        if not content:
            return response

        new_content: list[AnthropicContentBlock] = []
        modified = False

        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use = cast("AnthropicToolUseBlock", block)
                if tool_use.get("name") == BASH_TOOL_NAME:
                    command = _extract_bash_command(tool_use.get("input", {}))
                    blocked_msg = await self._maybe_block_command(command, context)
                    if blocked_msg is not None:
                        new_content.append({"type": "text", "text": blocked_msg})
                        modified = True
                        logger.info("Blocked supply chain install in non-streaming response")
                        continue
            new_content.append(block)

        if not modified:
            return response

        modified_response = dict(response)
        modified_response["content"] = new_content
        has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in new_content)
        if not has_tool_use and modified_response.get("stop_reason") == "tool_use":
            modified_response["stop_reason"] = "end_turn"
        return cast("AnthropicResponse", modified_response)

    # ========================================================================
    # Outgoing: streaming
    # ========================================================================

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer Bash tool_use blocks until complete and decide whether to emit them."""
        if isinstance(event, RawContentBlockStartEvent):
            return self._handle_content_block_start(event, context)
        if isinstance(event, RawContentBlockDeltaEvent):
            return self._handle_content_block_delta(event, context)
        if isinstance(event, RawContentBlockStopEvent):
            return await self._handle_content_block_stop(event, context)
        return [event]

    def _handle_content_block_start(
        self, event: RawContentBlockStartEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        block = event.content_block
        if isinstance(block, ToolUseBlock) and block.name == BASH_TOOL_NAME:
            self._buffered(context)[event.index] = _BufferedBashToolUse(id=block.id, name=block.name)
            return []  # suppress until we decide
        return [event]

    def _handle_content_block_delta(
        self, event: RawContentBlockDeltaEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        buffered = self._buffered(context)
        if event.index in buffered and isinstance(event.delta, InputJSONDelta):
            buffered[event.index].input_json += event.delta.partial_json
            return []  # continue buffering
        return [event]

    async def _handle_content_block_stop(
        self, event: RawContentBlockStopEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        buffered_map = self._buffered(context)
        if event.index not in buffered_map:
            return [cast(MessageStreamEvent, event)]
        buffered = buffered_map.pop(event.index)

        command = _extract_command_from_json(buffered.input_json)
        blocked_msg = await self._maybe_block_command(command, context)

        if blocked_msg is not None:
            logger.info("Blocked supply chain install in streaming response")
            text_block = TextBlock(type="text", text="")
            start_event = RawContentBlockStartEvent(
                type="content_block_start", index=event.index, content_block=text_block
            )
            delta_event = RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=event.index,
                delta=TextDelta(type="text_delta", text=blocked_msg),
            )
            return [
                cast(MessageStreamEvent, start_event),
                cast(MessageStreamEvent, delta_event),
                cast(MessageStreamEvent, event),
            ]

        # Allowed: re-emit the original tool_use events.
        tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
        start_event = RawContentBlockStartEvent(
            type="content_block_start", index=event.index, content_block=tool_use_block
        )
        delta_event = RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=event.index,
            delta=InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}"),
        )
        return [
            cast(MessageStreamEvent, start_event),
            cast(MessageStreamEvent, delta_event),
            cast(MessageStreamEvent, event),
        ]

    async def _maybe_block_command(self, command: str | None, context: "PolicyContext") -> str | None:
        """Check a single Bash command and return the blocked message if it should be blocked."""
        if not command:
            return None

        analysis = analyze_command(command)

        # Hard-block: command contains a construct we can't safely parse
        # (e.g. `$(...)`, `| sh`, unknown package manager). OSV can't clear
        # this — refuse unconditionally.
        if analysis.hard_block_reason is not None:
            logger.info(
                "Supply chain guard hard-block: %s",
                analysis.hard_block_reason,
            )
            context.record_event(
                "policy.supply_chain_guard.hard_blocked",
                {
                    "summary": "Blocked unparseable install command",
                    "reason": analysis.hard_block_reason,
                },
            )
            return format_hard_block_message(analysis.hard_block_reason, command=command)

        packages = list(analysis.packages)
        if not packages:
            return None

        results = await self._check_packages(packages, context)
        if not self._should_block(results):
            return None

        blocking = self._blocking_subset(results)
        context.record_event(
            "policy.supply_chain_guard.blocked",
            {
                "summary": f"Blocked install of {len(blocking)} vulnerable package(s)",
                "packages": [
                    {
                        "ecosystem": r.package.ecosystem,
                        "name": r.package.name,
                        "max_severity": r.max_severity.label,
                        "error": r.error,
                    }
                    for r in blocking
                ],
            },
        )
        return format_blocked_message(blocking, self._threshold, command=command)

    # ========================================================================
    # Incoming request hook: scan tool_results from prior installs
    # ========================================================================

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Inject a system-prompt warning when a prior install was vulnerable."""
        messages: list[AnthropicMessage] = list(request.get("messages") or [])
        if not messages:
            return request

        last_message = messages[-1]
        if last_message.get("role") != "user":
            return request

        tool_results = _collect_tool_results(last_message)
        if not tool_results:
            return request

        commands = _commands_for_tool_results(tool_results, messages)
        if not commands:
            return request

        all_packages: list[PackageRef] = []
        for command in commands:
            all_packages.extend(parse_install_commands(command))
        if not all_packages:
            return request

        # De-duplicate packages before lookup.
        seen: set[tuple[str, str]] = set()
        unique_packages: list[PackageRef] = []
        for pkg in all_packages:
            key = (pkg.ecosystem, pkg.name)
            if key in seen:
                continue
            seen.add(key)
            unique_packages.append(pkg)

        results = await self._check_packages(unique_packages, context)
        blocking = filter_blocking(results, self._threshold)
        if not blocking:
            return request

        warning = format_incoming_warning(blocking, self._threshold)
        modified = dict(request)
        modified["system"] = _prepend_system_warning(request.get("system"), warning)

        context.record_event(
            "policy.supply_chain_guard.incoming_warning",
            {
                "summary": f"Detected {len(blocking)} previously-installed vulnerable package(s)",
                "packages": [
                    {
                        "ecosystem": r.package.ecosystem,
                        "name": r.package.name,
                        "max_severity": r.max_severity.label,
                    }
                    for r in blocking
                ],
            },
        )
        return cast("AnthropicRequest", modified)


# =============================================================================
# Module-level helpers (pure; kept here so the policy class stays focused)
# =============================================================================


def _extract_bash_command(tool_input: object) -> str | None:
    """Pull the ``command`` string out of a Bash tool_use input dict."""
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    return None


def _extract_command_from_json(raw_json: str) -> str | None:
    """Best-effort parse of the buffered Bash tool_use input JSON."""
    if not raw_json:
        return None
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    return _extract_bash_command(parsed)


def _collect_tool_results(message: "AnthropicMessage") -> list["AnthropicToolResultBlock"]:
    """Return every tool_result block in a user message."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        cast("AnthropicToolResultBlock", b) for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
    ]


def _commands_for_tool_results(
    tool_results: list["AnthropicToolResultBlock"],
    messages: list["AnthropicMessage"],
) -> list[str]:
    """For each tool_result, find the matching Bash tool_use by id and return its command.

    tool_results in the last user message are correlated with tool_use blocks
    in the preceding assistant messages by ``tool_use_id``.
    """
    wanted_ids = {tr.get("tool_use_id") for tr in tool_results if tr.get("tool_use_id")}
    if not wanted_ids:
        return []

    commands: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") != BASH_TOOL_NAME:
                continue
            if block.get("id") not in wanted_ids:
                continue
            command = _extract_bash_command(block.get("input"))
            if command:
                commands.append(command)
    return commands


def _prepend_system_warning(
    existing: "str | list[AnthropicSystemBlock] | None",
    warning: str,
) -> "str | list[AnthropicSystemBlock]":
    """Prepend a warning string to the request's ``system`` field.

    Handles both the string and the list-of-blocks forms, and the case where
    ``system`` is missing entirely.
    """
    if existing is None:
        return warning
    if isinstance(existing, str):
        return f"{warning}\n\n{existing}" if existing else warning
    # list of AnthropicSystemBlock
    warning_block: AnthropicSystemBlock = {"type": "text", "text": warning}  # type: ignore[typeddict-item]
    return [warning_block, *existing]


__all__ = ["SupplyChainGuardPolicy"]
