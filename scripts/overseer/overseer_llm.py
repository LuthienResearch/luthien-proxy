"""Overseer LLM -- analyzes turn output and generates next prompts via direct Anthropic API."""

import logging
import re
from dataclasses import dataclass

import anthropic
from scripts.overseer.stream_parser import TurnSummary

logger = logging.getLogger(__name__)

OVERSEER_SYSTEM_PROMPT = """\
You are a test overseer monitoring a Claude Code session running through a proxy gateway.
Your job is to:
1. Analyze the turn output for signs of proxy/gateway issues (NOT code quality issues)
2. Generate the next prompt to keep the session productive and exercising different proxy features

Focus on proxy reliability issues:
- Streaming errors or truncation
- Tool calls that didn't get results
- Session state corruption
- Unexpected errors from the gateway
- Cost or latency anomalies

Do NOT comment on the quality of code the session is writing. That's not your concern.

Respond in this exact format:

## Analysis
[1-2 sentences about what happened in this turn from a proxy health perspective]

## Anomalies
[List each anomaly as a bullet point, or "None" if no issues]

## Next Prompt
[The exact prompt to send for the next turn. Keep the session productive -- ask it to build more features, write tests, refactor, use different tools, etc.]
"""


@dataclass
class OverseerAnalysis:
    """Result of the overseer LLM analyzing a turn."""

    analysis: str
    anomalies: list[str]
    next_prompt: str


def build_analysis_prompt(summary: TurnSummary, task: str) -> str:
    """Build the prompt to send to the overseer LLM."""
    lines = [
        f"Original task: {task}",
        f"Turn {summary.turn_number} summary:",
        f"  Session ID: {summary.session_id}",
        f"  Success: {summary.is_success}",
        f"  Tools used: {', '.join(summary.tools_used) or 'none'}",
        f"  Tool calls: {summary.tool_call_count}, Tool results: {summary.tool_result_count}",
        f"  Cost: ${summary.cost_usd:.4f}",
        f"  Duration: {summary.duration_seconds:.1f}s",
        f"  Result text (first 500 chars): {summary.result_text[:500]}",
    ]
    if summary.anomalies:
        lines.append("  ANOMALIES DETECTED:")
        for a in summary.anomalies:
            lines.append(f"    - {a}")
    return "\n".join(lines)


def parse_overseer_response(response_text: str) -> OverseerAnalysis:
    """Parse the structured response from the overseer LLM."""
    analysis = ""
    anomalies: list[str] = []
    next_prompt = ""

    analysis_match = re.search(r"## Analysis\s*\n(.*?)(?=\n## )", response_text, re.DOTALL)
    if analysis_match:
        analysis = analysis_match.group(1).strip()

    anomalies_match = re.search(r"## Anomalies\s*\n(.*?)(?=\n## )", response_text, re.DOTALL)
    if anomalies_match:
        anomalies_text = anomalies_match.group(1).strip()
        if anomalies_text.lower() != "none":
            anomalies = [
                line.lstrip("- ").strip() for line in anomalies_text.split("\n") if line.strip().startswith("-")
            ]

    prompt_match = re.search(r"## Next Prompt\s*\n(.*)", response_text, re.DOTALL)
    if prompt_match:
        next_prompt = prompt_match.group(1).strip()

    if not next_prompt:
        logger.warning("Overseer LLM response missing '## Next Prompt' section — response may be malformed")

    return OverseerAnalysis(analysis=analysis, anomalies=anomalies, next_prompt=next_prompt)


async def analyze_turn(
    summary: TurnSummary,
    task: str,
    model: str = "claude-haiku-4-5-20251001",
    client: anthropic.AsyncAnthropic | None = None,
) -> OverseerAnalysis:
    """Call the Anthropic API directly to analyze a turn and get the next prompt."""
    if client is None:
        client = anthropic.AsyncAnthropic()

    prompt = build_analysis_prompt(summary, task)
    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=OVERSEER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    if not response.content or not hasattr(response.content[0], "text"):
        return OverseerAnalysis(analysis="", anomalies=["Empty overseer LLM response"], next_prompt="")
    response_text = response.content[0].text
    return parse_overseer_response(response_text)
