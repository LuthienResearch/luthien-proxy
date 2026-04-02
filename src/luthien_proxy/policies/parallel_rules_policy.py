"""ParallelRulesPolicy — apply multiple rewriting rules in parallel.

Each rule gets an independent copy of the text and rewrites it via an LLM call.
If multiple rules produce different rewrites, a refinement round merges them
into a single comprehensive version.

Can be used standalone with static rules in YAML config, or driven dynamically
by another policy that sets rules via request state using set_rules_for_request().

**Latency note:** Each rule fires one LLM call (in parallel via asyncio.gather).
If 2+ rules apply, an additional refinement call merges them. With N rules, the
worst case is N+1 LLM calls per text block. Because this policy extends
SimplePolicy, streaming content is buffered — the client sees no output until all
rule calls complete. Keep rule count low (default max_rules=5) to limit latency.

Example config (static rules):
    policy:
      class: "luthien_proxy.policies.parallel_rules_policy:ParallelRulesPolicy"
      config:
        model: "claude-haiku-4-5"
        rules:
          - name: "no-jargon"
            instruction: "Rewrite to remove unnecessary jargon. Keep technical terms only when essential."
          - name: "concise"
            instruction: "Make the text more concise. Remove filler words and redundant phrases."
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from luthien_proxy.policies.rules_llm_utils import call_llm
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
)
from luthien_proxy.policy_core.anthropic_hook_policy import AnthropicHookPolicy
from luthien_proxy.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

DEFAULT_MAX_RULES = 5

_FENCED_BLOCK_RE = re.compile(r"^`{3,}\w*\n(.*?)`{3,}\s*$", re.DOTALL)


@dataclass(frozen=True)
class Rule:
    """A named rewriting rule with an LLM instruction."""

    name: str
    instruction: str


class ParallelRulesConfig(BaseModel):
    """Configuration for ParallelRulesPolicy."""

    model: str = Field(
        default="claude-haiku-4-5",
        description="LiteLLM model string for rule application and refinement",
    )
    api_base: str | None = Field(default=None, description="Optional API base URL override")
    api_key: str | None = Field(
        default=None,
        description="API key for LLM calls (falls back to env vars)",
        json_schema_extra={"format": "password"},
    )
    temperature: float = Field(default=0.0, description="Sampling temperature for rule LLM calls")
    max_tokens: int = Field(default=4096, description="Max output tokens per rule application")
    max_rules: int = Field(
        default=DEFAULT_MAX_RULES,
        description="Maximum number of rules to apply per response. Excess rules are silently dropped.",
    )
    rules: list[dict[str, str]] = Field(
        default_factory=list,
        description="Static rules list. Each item has 'name' and 'instruction' keys.",
    )

    model_config = {"frozen": True}


@dataclass
class _ParallelRulesState:
    """Per-request state: rules to apply on this turn."""

    rules: list[Rule] = field(default_factory=list)


@dataclass(frozen=True)
class _RuleResult:
    """Result of applying a single rule."""

    rule: Rule
    rewritten: str
    changed: bool


def _parse_rule_decision(raw: str) -> tuple[bool, str] | None:
    """Parse a structured rule response into (apply, rewritten_text).

    Returns None if the response can't be parsed.
    """
    text = raw.strip()

    # Strip markdown fences if present (handles ```, ```json, `````, etc.)
    match = _FENCED_BLOCK_RE.match(text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict) or "apply" not in data:
        return None

    apply = bool(data["apply"])
    rewritten = str(data.get("rewritten", "")) if apply else ""
    return (apply, rewritten)


class ParallelRulesPolicy(SimplePolicy):
    """Apply multiple rewriting rules in parallel with LLM-based refinement.

    For each response text block:
    1. Fan out — each rule gets an LLM call that decides whether to apply
       and provides a rewrite if so (structured JSON response)
    2. Collect — gather decisions from all rules
    3. Merge — if 0-1 rules applied, use that version directly.
       If 2+ rules applied, run a refinement LLM call that sees all
       versions and produces a comprehensive merge.

    **Cost model:** N rules → N parallel LLM calls + 1 refinement if 2+ apply.
    Streaming is buffered (SimplePolicy), so latency = slowest rule call + refinement.
    """

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> "AsyncIterator[AnthropicPolicyEmission]":
        """Delegate to AnthropicHookPolicy.run_anthropic.

        Explicit override avoids a Python 3.13 runtime_checkable Protocol
        MRO issue where the Protocol's abstract stub can shadow the concrete
        implementation from AnthropicHookPolicy in some environments.
        """
        return AnthropicHookPolicy.run_anthropic(self, io, context)

    def __init__(self, config: ParallelRulesConfig | None = None) -> None:
        """Initialize with config and convert static rules to immutable tuple."""
        parsed = self._init_config(config, ParallelRulesConfig)
        settings = get_settings()
        self.config = ParallelRulesConfig(
            model=settings.llm_judge_model or parsed.model,
            api_base=settings.llm_judge_api_base or parsed.api_base,
            api_key=parsed.api_key,
            temperature=parsed.temperature,
            max_tokens=parsed.max_tokens,
            max_rules=parsed.max_rules,
            rules=parsed.rules,
        )
        self._fallback_api_key: str | None = settings.llm_judge_api_key or settings.litellm_master_key or None
        self._static_rules: tuple[Rule, ...] = tuple(
            Rule(name=r["name"], instruction=r["instruction"]) for r in self.config.rules
        )

    @property
    def short_policy_name(self) -> str:
        """Human-readable policy name."""
        return "ParallelRules"

    def _get_rules(self, context: "PolicyContext") -> list[Rule]:
        """Get rules from request state (dynamic) or config (static), capped at max_rules."""
        state = context.get_request_state(self, _ParallelRulesState, _ParallelRulesState)
        rules = state.rules if state.rules else list(self._static_rules)
        if len(rules) > self.config.max_rules:
            logger.warning("Truncating %d rules to max_rules=%d", len(rules), self.config.max_rules)
            rules = rules[: self.config.max_rules]
        return rules

    def set_rules_for_request(self, context: "PolicyContext", rules: list[Rule]) -> None:
        """Set dynamic rules for this request (called by ClaudeMdRulesPolicy)."""
        state = context.get_request_state(self, _ParallelRulesState, _ParallelRulesState)
        state.rules = rules

    def _resolve_api_key(self, context: "PolicyContext") -> str | None:
        """Resolve API key: explicit config → client passthrough → env fallback."""
        return self._resolve_judge_api_key(context, self.config.api_key, self._fallback_api_key)

    async def _call_llm(
        self,
        messages: list[dict[str, str]],
        context: "PolicyContext",
        *,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """Call LLM using this policy's config and the resolved API key."""
        return await call_llm(
            messages,
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            api_base=self.config.api_base,
            api_key=self._resolve_api_key(context),
            response_format=response_format,
        )

    async def simple_on_response_content(self, content: str, context: "PolicyContext") -> str:
        """Apply all rules in parallel, merge if needed."""
        rules = self._get_rules(context)
        if not rules:
            return content

        results = await asyncio.gather(*(self._apply_rule(rule, content, context) for rule in rules))

        changed_results = [r for r in results if r.changed]

        if not changed_results:
            return content
        if len(changed_results) == 1:
            return changed_results[0].rewritten

        return await self._refine(content, changed_results, context)

    async def _apply_rule(self, rule: Rule, text: str, context: "PolicyContext") -> _RuleResult:
        """Apply a single rule: LLM decides whether to apply and provides rewrite if so."""
        try:
            raw = await self._call_llm(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a text rewriting assistant. You will be given a rule and a text.\n"
                            "First decide whether the rule applies to this text. "
                            "If it does, rewrite the text with the rule applied.\n\n"
                            f"Rule: {rule.instruction}\n\n"
                            "Respond with a JSON object:\n"
                            '- If the rule applies: {"apply": true, "rewritten": "<the rewritten text>"}\n'
                            '- If the rule does not apply: {"apply": false}\n\n'
                            "Return ONLY the JSON object. No markdown fences, no commentary."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                context,
                response_format={"type": "json_object"},
            )
            decision = _parse_rule_decision(raw)
            if decision is None:
                logger.warning("Rule '%s' returned unparseable response, treating as no-change", rule.name)
                return _RuleResult(rule=rule, rewritten=text, changed=False)

            apply, rewritten = decision
            if not apply:
                return _RuleResult(rule=rule, rewritten=text, changed=False)

            return _RuleResult(rule=rule, rewritten=rewritten.strip(), changed=True)

        except Exception:
            logger.exception("Rule '%s' failed, treating as no-change", rule.name)
            return _RuleResult(rule=rule, rewritten=text, changed=False)

    async def _refine(self, original: str, changed_results: list[_RuleResult], context: "PolicyContext") -> str:
        """Merge multiple rule rewrites into a single comprehensive version."""
        versions_text = "\n\n".join(
            f"--- Rule: {r.rule.name} ({r.rule.instruction}) ---\n{r.rewritten}" for r in changed_results
        )

        try:
            return await self._call_llm(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a text editor merging multiple rewrites of the same text. "
                            "Each rewrite applied a different rule independently. "
                            "Your job is to produce a single version that faithfully applies ALL rules. "
                            "Return ONLY the merged text. No commentary or explanation."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Original text:\n{original}\n\n"
                            f"Rewritten versions (each applied one rule):\n{versions_text}\n\n"
                            f"Produce a single version that applies all {len(changed_results)} rules together."
                        ),
                    },
                ],
                context,
            )
        except Exception:
            logger.exception("Refinement failed, returning original text unmodified")
            return original


__all__ = ["ParallelRulesPolicy", "Rule"]
