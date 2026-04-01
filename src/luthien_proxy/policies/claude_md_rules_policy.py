"""ClaudeMdRulesPolicy — extract rules from CLAUDE.md and apply via ParallelRulesPolicy.

On the first turn of a session, scans the system prompt for CLAUDE.md content,
calls an LLM to extract objective rules, and persists them to the database.
On subsequent turns, loads persisted rules and delegates to ParallelRulesPolicy.

Example config:
    policy:
      class: "luthien_proxy.policies.claude_md_rules_policy:ClaudeMdRulesPolicy"
      config:
        model: "claude-haiku-4-5"
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from luthien_proxy.policies.parallel_rules_policy import ParallelRulesPolicy, Rule
from luthien_proxy.policies.rules_llm_utils import call_llm
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.storage.session_rules import (
    SessionRule,
    has_rules,
    load_rules,
    save_rules,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from luthien_proxy.llm.types.anthropic import AnthropicRequest
    from luthien_proxy.policy_core.anthropic_execution_interface import (
        AnthropicPolicyEmission,
        AnthropicPolicyIOProtocol,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """\
You extract objective, enforceable rules from developer instruction files (like CLAUDE.md).

Given the content of a CLAUDE.md file, extract rules that an AI assistant should follow.
Focus on concrete, actionable instructions — not vague preferences or context.

Return a JSON array of objects, each with "name" (short slug) and "instruction" (the rule).
If no extractable rules exist, return an empty array: []

Example output:
[
  {"name": "no-emoji", "instruction": "Never use emoji in responses unless the user explicitly requests it."},
  {"name": "early-returns", "instruction": "Prefer early returns over nested if statements to reduce indentation."}
]

Return ONLY the JSON array. No markdown fencing, no commentary."""


class ClaudeMdRulesConfig(BaseModel):
    """Configuration for ClaudeMdRulesPolicy."""

    model: str = Field(
        default="claude-haiku-4-5",
        description="LiteLLM model string for rule extraction",
    )
    api_base: str | None = Field(default=None, description="Optional API base URL override")
    api_key: str | None = Field(
        default=None,
        description="API key for LLM calls (falls back to env vars)",
        json_schema_extra={"format": "password"},
    )
    temperature: float = Field(default=0.0, description="Sampling temperature for extraction")
    max_tokens: int = Field(default=4096, description="Max output tokens for extraction")

    model_config = {"frozen": True}


class ClaudeMdRulesPolicy(BasePolicy):
    """Extract rules from CLAUDE.md at session start and apply via ParallelRulesPolicy.

    On each request:
    1. If no session_id or db_pool → passthrough (no rule persistence possible)
    2. If rules already in DB for this session → load and apply
    3. If first turn → scan for CLAUDE.md content, extract rules, save to DB, apply
    """

    def __init__(self, config: ClaudeMdRulesConfig | dict[str, Any] | None = None) -> None:
        """Initialize with extraction config and inner ParallelRulesPolicy."""
        self.config = self._init_config(config, ClaudeMdRulesConfig)
        self._parallel_rules = ParallelRulesPolicy(
            config={
                "model": self.config.model,
                "api_base": self.config.api_base,
                "api_key": self.config.api_key,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }
        )

    @property
    def short_policy_name(self) -> str:
        """Human-readable policy name."""
        return "ClaudeMdRules"

    async def run_anthropic(
        self,
        io: "AnthropicPolicyIOProtocol",
        context: "PolicyContext",
    ) -> "AsyncIterator[AnthropicPolicyEmission]":
        """Ensure rules are loaded/extracted, then delegate to ParallelRulesPolicy."""
        rules = await self._ensure_rules(io.request, context)
        if rules:
            self._parallel_rules.set_rules_for_request(
                context, [Rule(name=r.name, instruction=r.instruction) for r in rules]
            )

        async for emission in self._parallel_rules.run_anthropic(io, context):
            yield emission

    async def _ensure_rules(
        self,
        request: "AnthropicRequest",
        context: "PolicyContext",
    ) -> list[SessionRule]:
        """Load rules from DB, or extract from CLAUDE.md on first turn."""
        if not context.session_id or not context.db_pool:
            return []

        already_extracted = await has_rules(context.db_pool, context.session_id)
        if already_extracted:
            return await load_rules(context.db_pool, context.session_id)

        claude_md_content = _find_claude_md_content(request)
        if not claude_md_content:
            # No CLAUDE.md found — save empty sentinel to avoid re-scanning
            await save_rules(context.db_pool, context.session_id, [])
            return []

        try:
            rules = await self._extract_rules(claude_md_content)
        except Exception:
            logger.exception("Failed to extract rules from CLAUDE.md")
            # Save empty sentinel so we don't retry on every turn
            await save_rules(context.db_pool, context.session_id, [])
            return []

        await save_rules(context.db_pool, context.session_id, rules)
        context.record_event(
            "policy.claude_md_rules.extracted",
            {"rule_count": len(rules), "rule_names": [r.name for r in rules]},
        )
        return rules

    async def _extract_rules(self, content: str) -> list[SessionRule]:
        """Call LLM to extract rules from CLAUDE.md content."""
        llm_output = await call_llm(
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
        )
        return _parse_extracted_rules(llm_output)


def _find_claude_md_content(request: "AnthropicRequest") -> str | None:
    """Scan system prompt and first user message for CLAUDE.md content.

    Looks for common indicators: "CLAUDE.md", "claude.md", "Contents of",
    "system-reminder" tags containing instruction blocks.
    """
    candidates: list[str] = []

    # Check the system field (Anthropic API puts system prompt here)
    system = request.get("system")
    if isinstance(system, str):
        candidates.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    candidates.append(text)

    # Check the first user message (some clients embed instructions there)
    messages = request.get("messages", [])
    if messages:
        first_msg = messages[0]
        if first_msg.get("role") == "user":
            content = first_msg.get("content")
            if isinstance(content, str):
                candidates.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if isinstance(text, str):
                            candidates.append(text)

    claude_md_indicators = ("CLAUDE.md", "claude.md", "claudeMd", "system-reminder")
    matching = [c for c in candidates if any(indicator in c for indicator in claude_md_indicators)]

    if not matching:
        return None

    return "\n\n".join(matching)


def _parse_extracted_rules(llm_output: str) -> list[SessionRule]:
    """Parse JSON array of rules from LLM output.

    Handles common LLM quirks: markdown code fences, leading text before JSON.
    """
    text = llm_output.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Find the JSON array in the output
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        logger.warning("No JSON array found in extraction output: %.200s", text)
        return []

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON from extraction output: %.200s", text)
        return []

    if not isinstance(parsed, list):
        return []

    rules: list[SessionRule] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        instruction = item.get("instruction")
        if isinstance(name, str) and isinstance(instruction, str) and name and instruction:
            rules.append(SessionRule(name=name, instruction=instruction))

    return rules


__all__ = ["ClaudeMdRulesPolicy"]
