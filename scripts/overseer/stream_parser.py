"""Parse Claude Code stream-json output into structured events with turn summarization.

Claude Code's `--output-format stream-json` emits newline-delimited JSON with event
types like system/init, assistant, user, and result. This module extracts those events
and summarizes a full turn with rule-based anomaly detection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class StreamEvent:
    """A single parsed event from Claude Code stream-json output."""

    type: str
    subtype: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return self.raw.get("session_id", "")

    @property
    def is_result(self) -> bool:
        return self.type == "result"

    @property
    def is_success(self) -> bool:
        return self.is_result and self.subtype == "success"

    def get_tool_uses(self) -> list[dict]:
        """Extract tool_use blocks from assistant messages."""
        if self.type != "assistant":
            return []
        content = self.raw.get("message", {}).get("content", [])
        return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]

    def get_tool_results(self) -> list[dict]:
        """Extract tool_result blocks from user messages."""
        if self.type != "user":
            return []
        content = self.raw.get("message", {}).get("content", [])
        return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]

    def get_text(self) -> str:
        """Extract concatenated text from assistant message content blocks."""
        if self.type != "assistant":
            return ""
        content = self.raw.get("message", {}).get("content", [])
        texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return " ".join(texts)


@dataclass
class TurnSummary:
    """Summarized metadata for a single overseer turn."""

    turn_number: int
    session_id: str
    is_success: bool
    tools_used: list[str]
    tool_call_count: int
    tool_result_count: int
    cost_usd: float
    duration_seconds: float
    result_text: str
    anomalies: list[str]
    num_turns_reported: int


def parse_stream_json(output: str) -> list[StreamEvent]:
    """Parse newline-delimited JSON into StreamEvent objects.

    Malformed lines are silently skipped.
    """
    events: list[StreamEvent] = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(
            StreamEvent(
                type=data.get("type", "unknown"),
                subtype=data.get("subtype"),
                raw=data,
            )
        )
    return events


def summarize_turn(
    raw_output: str,
    turn_number: int,
    start_time: float,
    end_time: float,
    slow_threshold: float = 180.0,
) -> TurnSummary:
    """Build a TurnSummary from raw stream-json output with anomaly detection.

    Anomaly rules:
    - Result event has is_error=True
    - Duration exceeds slow_threshold
    - Tool calls present with no corresponding tool results
    """
    events = parse_stream_json(raw_output)
    duration = end_time - start_time

    # Collect tool call and result counts
    all_tool_uses: list[dict] = []
    all_tool_results: list[dict] = []
    for event in events:
        all_tool_uses.extend(event.get_tool_uses())
        all_tool_results.extend(event.get_tool_results())

    tool_names = list(dict.fromkeys(use.get("name", "") for use in all_tool_uses))

    # Extract metadata from result event
    session_id = ""
    is_success = False
    result_text = ""
    cost_usd = 0.0
    num_turns_reported = 0
    is_error = False

    for event in events:
        if event.session_id and not session_id:
            session_id = event.session_id
        if event.is_result:
            is_success = event.is_success
            result_text = event.raw.get("result", "")
            cost_usd = event.raw.get("total_cost_usd", 0.0)
            num_turns_reported = event.raw.get("num_turns", 0)
            is_error = event.raw.get("is_error", False)

    # Anomaly detection
    anomalies: list[str] = []

    if is_error:
        anomalies.append(f"Error result: {result_text[:120]}")

    if duration > slow_threshold:
        anomalies.append(f"Slow turn: {duration:.1f}s exceeds {slow_threshold:.0f}s threshold")

    if all_tool_uses and not all_tool_results:
        anomalies.append(f"Tool calls with no tool results: {', '.join(tool_names)}")

    return TurnSummary(
        turn_number=turn_number,
        session_id=session_id,
        is_success=is_success,
        tools_used=tool_names,
        tool_call_count=len(all_tool_uses),
        tool_result_count=len(all_tool_results),
        cost_usd=cost_usd,
        duration_seconds=duration,
        result_text=result_text,
        anomalies=anomalies,
        num_turns_reported=num_turns_reported,
    )
