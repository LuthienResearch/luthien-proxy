"""ParallelRulesPolicy — apply multiple rewriting rules in parallel.

Each rule gets an independent copy of the text and rewrites it via an LLM call.
If multiple rules produce different rewrites, a refinement round merges them
into a single comprehensive version.

Can be used standalone with static rules in YAML config, or driven dynamically
by another policy (like ClaudeMdRulesPolicy) that sets rules via request state.

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
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from luthien_proxy.policies.rules_llm_utils import call_llm
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.storage.session_rules import SessionRule

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

DEFAULT_MAX_RULES = 10


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

    rules: list[SessionRule] = field(default_factory=list)


@dataclass(frozen=True)
class _RuleResult:
    """Result of applying a single rule."""

    rule: SessionRule
    rewritten: str
    changed: bool


class ParallelRulesPolicy(SimplePolicy):
    """Apply multiple rewriting rules in parallel with LLM-based refinement.

    For each response text block:
    1. Fan out — each rule rewrites the text independently via an LLM call
    2. Collect — compare results to the original
    3. Merge — if 0-1 rules changed the text, use that version directly.
       If 2+ rules changed it, run a refinement LLM call that sees all
       versions and produces a comprehensive merge.
    """

    def __init__(self, config: ParallelRulesConfig | dict[str, Any] | None = None) -> None:
        """Initialize with config and convert static rules to immutable tuple."""
        self.config = self._init_config(config, ParallelRulesConfig)
        self._static_rules: tuple[SessionRule, ...] = tuple(
            SessionRule(name=r["name"], instruction=r["instruction"]) for r in self.config.rules
        )

    @property
    def short_policy_name(self) -> str:
        """Human-readable policy name."""
        return "ParallelRules"

    def _get_rules(self, context: "PolicyContext") -> list[SessionRule]:
        """Get rules from request state (dynamic) or config (static), capped at max_rules."""
        state = context.get_request_state(self, _ParallelRulesState, _ParallelRulesState)
        rules = state.rules if state.rules else list(self._static_rules)
        if len(rules) > self.config.max_rules:
            logger.warning("Truncating %d rules to max_rules=%d", len(rules), self.config.max_rules)
            rules = rules[: self.config.max_rules]
        return rules

    def set_rules_for_request(self, context: "PolicyContext", rules: list[SessionRule]) -> None:
        """Set dynamic rules for this request (called by ClaudeMdRulesPolicy)."""
        state = context.get_request_state(self, _ParallelRulesState, _ParallelRulesState)
        state.rules = rules

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call LLM using this policy's config."""
        return await call_llm(
            messages,
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
        )

    async def simple_on_response_content(self, content: str, context: "PolicyContext") -> str:
        """Apply all rules in parallel, merge if needed."""
        rules = self._get_rules(context)
        if not rules:
            return content

        results = await asyncio.gather(*(self._apply_rule(rule, content) for rule in rules))

        changed_results = [r for r in results if r.changed]

        if not changed_results:
            return content
        if len(changed_results) == 1:
            return changed_results[0].rewritten

        return await self._refine(content, changed_results)

    async def _apply_rule(self, rule: SessionRule, text: str) -> _RuleResult:
        """Apply a single rule to the text via LLM call."""
        try:
            rewritten = await self._call_llm(
                [
                    {
                        "role": "system",
                        "content": (
                            f"You are a text rewriting assistant. Apply the following rule to the user's text.\n\n"
                            f"Rule: {rule.instruction}\n\n"
                            f"Return ONLY the rewritten text with the rule applied. "
                            f"Do not add commentary, explanations, or meta-text. "
                            f"If no changes are needed, return the text unchanged."
                        ),
                    },
                    {"role": "user", "content": text},
                ]
            )
            rewritten = rewritten.strip()
            changed = rewritten != text.strip()
            return _RuleResult(rule=rule, rewritten=rewritten, changed=changed)

        except Exception:
            logger.exception("Rule '%s' failed, treating as no-change", rule.name)
            return _RuleResult(rule=rule, rewritten=text, changed=False)

    async def _refine(self, original: str, changed_results: list[_RuleResult]) -> str:
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
                ]
            )
        except Exception:
            logger.exception("Refinement failed, using first changed result as fallback")
            return changed_results[0].rewritten


__all__ = ["ParallelRulesPolicy"]
