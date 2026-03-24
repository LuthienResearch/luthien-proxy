"""ClaudeMdRulesPolicy — extract rules from CLAUDE.md and apply them per-session.

On the first turn of a session:
1. Scan the system prompt / conversation for CLAUDE.md content
2. Call an LLM to extract objective, mechanically-applicable rules
3. Store extracted rules in the database keyed by session_id

On subsequent turns:
1. Load rules from the database
2. Delegate to ParallelRulesPolicy to apply them

If no session_id is available or no CLAUDE.md content is found,
the policy passes through without modification.

Example config:
    policy:
      class: "luthien_proxy.policies.claude_md_rules_policy:ClaudeMdRulesPolicy"
      config:
        model: "claude-haiku-4-5"
        parallel_rules_config:
          model: "claude-haiku-4-5"
          max_tokens: 4096
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from luthien_proxy.policies.parallel_rules_policy import ParallelRulesPolicy
from luthien_proxy.policies.rules_llm_utils import call_llm
from luthien_proxy.policy_core import BasePolicy
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
)
from luthien_proxy.storage.session_rules import SessionRule, has_rules, load_rules, save_rules

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


RULE_EXTRACTION_PROMPT = """\
You are analyzing a CLAUDE.md (or similar configuration) file to extract objective, \
mechanically-applicable rules for rewriting AI assistant responses.

Extract rules that are:
- Concrete and actionable (not vague preferences)
- About response FORMAT, STYLE, or CONTENT (not about workflow or project structure)
- Mechanically applicable to any response text

Examples of good rules to extract:
- "No emojis unless explicitly requested"
- "Use early returns over nested ifs in code"
- "Prefer f-strings over .format()"
- "Only WHY comments, never WHAT comments"
- "Keep responses concise — lead with the answer, not reasoning"

Do NOT extract rules about:
- Project structure or file organization
- Git workflow or commit conventions
- Tool usage or permissions
- Things that require understanding full project context

Return a JSON array of objects, each with "name" (short identifier) and "instruction" \
(the full rule as a rewriting instruction). Return an empty array [] if no applicable rules are found.

Example output:
[
  {"name": "no-emojis", "instruction": "Remove all emojis from the text unless the user explicitly requested emojis."},
  {"name": "concise-style", "instruction": "Make responses concise. Lead with the answer or action, not reasoning. Skip filler words and preamble."}
]
"""


class ClaudeMdRulesConfig(BaseModel):
    """Configuration for ClaudeMdRulesPolicy."""

    model: str = Field(
        default="claude-haiku-4-5",
        description="LiteLLM model for extracting rules from CLAUDE.md",
    )
    api_base: str | None = Field(default=None, description="Optional API base URL override")
    api_key: str | None = Field(
        default=None,
        description="API key for extraction LLM calls",
        json_schema_extra={"format": "password"},
    )
    temperature: float = Field(default=0.0, description="Temperature for rule extraction")
    max_tokens: int = Field(default=4096, description="Max tokens for rule extraction response")
    parallel_rules_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Config passed to the inner ParallelRulesPolicy",
    )

    model_config = {"frozen": True}


class ClaudeMdRulesPolicy(BasePolicy, AnthropicExecutionInterface):
    """Extract rules from CLAUDE.md at session start, apply on all turns.

    Wraps a ParallelRulesPolicy and manages rule extraction + persistence.
    On first turn: extract rules → store in DB → apply.
    On subsequent turns: load from DB → apply.
    """

    def __init__(self, config: ClaudeMdRulesConfig | dict[str, Any] | None = None) -> None:
        """Initialize with extraction config and inner ParallelRulesPolicy."""
        self.config = self._init_config(config, ClaudeMdRulesConfig)
        self._parallel_policy = ParallelRulesPolicy(self.config.parallel_rules_config)

    @property
    def short_policy_name(self) -> str:
        """Human-readable policy name."""
        return "ClaudeMdRules"

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Ensure rules exist, then delegate to ParallelRulesPolicy."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            rules = await self._ensure_rules(io, context)

            if rules:
                self._parallel_policy.set_rules_for_request(context, rules)

            async for emission in self._parallel_policy.run_anthropic(io, context):
                yield emission

        return _run()

    async def _ensure_rules(self, io: AnthropicPolicyIOProtocol, context: "PolicyContext") -> list[SessionRule]:
        """Load existing rules or extract new ones from CLAUDE.md content."""
        session_id = context.session_id
        db_pool = context.db_pool

        if not session_id or not db_pool:
            logger.debug("No session_id or db_pool — skipping rule extraction")
            return []

        if await has_rules(db_pool, session_id):
            rules = await load_rules(db_pool, session_id)
            logger.debug("Loaded %d rules for session %s", len(rules), session_id[:12])
            return rules

        # First turn: extract rules from request content
        claude_md_content = self._find_claude_md_content(io.request)
        if not claude_md_content:
            logger.debug("No CLAUDE.md content found in request")
            # Store empty rules to avoid re-scanning on every turn
            await save_rules(db_pool, session_id, [])
            return []

        rules = await self._extract_rules(claude_md_content)
        await save_rules(db_pool, session_id, rules)
        logger.info("Extracted %d rules from CLAUDE.md for session %s", len(rules), session_id[:12])
        return rules

    def _find_claude_md_content(self, request: Any) -> str | None:
        """Search the request for CLAUDE.md content.

        Looks in the system prompt and message content for text that appears to
        be from CLAUDE.md (contains configuration/instruction patterns).
        """
        parts: list[str] = []

        # Check system prompt
        system = request.get("system")
        if isinstance(system, str):
            parts.append(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str):
                        parts.append(text)

        # Check first user message for system-reminder tags containing CLAUDE.md
        messages = request.get("messages", [])
        if messages:
            first_msg = messages[0]
            content = first_msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if isinstance(text, str):
                            parts.append(text)

        # Look for CLAUDE.md-like content
        full_text = "\n".join(parts)
        if not full_text:
            return None

        # Heuristic: CLAUDE.md content typically contains instruction-like patterns
        claude_md_indicators = ["CLAUDE.md", "claude.md", "## Coding Style", "## Style", "## Rules"]
        has_indicator = any(indicator in full_text for indicator in claude_md_indicators)
        if not has_indicator:
            return None

        return full_text

    async def _extract_rules(self, claude_md_content: str) -> list[SessionRule]:
        """Call LLM to extract objective rules from CLAUDE.md content."""
        try:
            content = await call_llm(
                [
                    {"role": "system", "content": RULE_EXTRACTION_PROMPT},
                    {"role": "user", "content": f"Extract rules from this configuration:\n\n{claude_md_content}"},
                ],
                model=self.config.model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                api_base=self.config.api_base,
                api_key=self.config.api_key,
            )
            return self._parse_extracted_rules(content)

        except Exception:
            logger.exception("Rule extraction LLM call failed")
            return []

    def _parse_extracted_rules(self, llm_output: str) -> list[SessionRule]:
        """Parse the LLM's JSON output into SessionRule objects."""
        text = llm_output.strip()

        # Handle fenced code blocks
        if text.startswith("```"):
            text = text.lstrip("`")
            nl = text.find("\n")
            if nl != -1:
                prefix = text[:nl].strip().lower()
                if prefix in {"json", ""}:
                    text = text[nl + 1 :]
            text = text.rstrip("`").strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse rule extraction response as JSON")
            return []

        if not isinstance(data, list):
            logger.warning("Rule extraction response is not a JSON array")
            return []

        rules = []
        for item in data:
            if isinstance(item, dict) and "name" in item and "instruction" in item:
                rules.append(SessionRule(name=str(item["name"]), instruction=str(item["instruction"])))
            else:
                logger.warning("Skipping malformed rule entry: %s", item)

        return rules


__all__ = ["ClaudeMdRulesPolicy"]
