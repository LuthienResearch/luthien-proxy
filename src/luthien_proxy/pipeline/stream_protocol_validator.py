"""Validator for Anthropic streaming protocol compliance.

Enforces the event ordering invariants documented in Anthropic's streaming API:

    message_start
    ├── content_block_start(0)
    │   ├── content_block_delta(0) ...
    │   └── content_block_stop(0)
    ├── content_block_start(1)
    │   ├── content_block_delta(1) ...
    │   └── content_block_stop(1)
    ├── ...
    message_delta   (stop_reason, usage)
    message_stop

Key invariants:
  1. message_start is the first event
  2. message_stop is the last event
  3. All content_block_* events precede message_delta
  4. Each content block has start → delta(s) → stop lifecycle
  5. Block indices are non-negative and start events use monotonically increasing indices
  6. No content_block_delta/stop without a preceding start for that index

Not yet covered:
  - Thinking/redacted_thinking blocks must precede text blocks (see gotchas.md).
    This is a content-type ordering constraint, not a structural event ordering
    constraint, and would require inspecting content_block_start payloads.

Works with both raw event dicts (from SSE parsing in e2e tests) and
Pydantic model objects (from unit tests using anthropic SDK types).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Event types that are part of the content block lifecycle
_CONTENT_BLOCK_EVENTS = frozenset({"content_block_start", "content_block_delta", "content_block_stop"})


@dataclass
class StreamViolation:
    """A single protocol violation found during validation."""

    rule: str
    message: str
    event_index: int
    event_type: str


@dataclass
class StreamValidationResult:
    """Result of validating an Anthropic event stream."""

    violations: list[StreamViolation] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """Whether the event stream passed all protocol checks."""
        return len(self.violations) == 0

    def assert_valid(self) -> None:
        """Raise AssertionError with details if any violations were found."""
        if self.valid:
            return
        lines = ["Anthropic streaming protocol violations found:"]
        for v in self.violations:
            lines.append(f"  [{v.event_index}] {v.rule}: {v.message} (event: {v.event_type})")
        raise AssertionError("\n".join(lines))


def _get_event_type(event: dict | object) -> str | None:
    """Extract event type from a dict or Pydantic model."""
    if isinstance(event, dict):
        return event.get("type")
    return getattr(event, "type", None)


def _get_block_index(event: dict | object) -> int | None:
    """Extract block index from a content_block_* event."""
    if isinstance(event, dict):
        return event.get("index")
    return getattr(event, "index", None)


def validate_anthropic_event_ordering(
    events: list,
) -> StreamValidationResult:
    """Validate that a list of Anthropic streaming events follows protocol ordering.

    Args:
        events: List of event dicts or Pydantic model objects. Each must have
                a ``type`` field/attribute.

    Returns:
        StreamValidationResult with any violations found.
    """
    result = StreamValidationResult()

    if not events:
        result.violations.append(
            StreamViolation(
                rule="non_empty",
                message="Event stream is empty",
                event_index=-1,
                event_type="(none)",
            )
        )
        return result

    event_types = [_get_event_type(e) for e in events]

    # --- Rule 1: message_start must be first ---
    if event_types[0] != "message_start":
        result.violations.append(
            StreamViolation(
                rule="message_start_first",
                message=f"First event must be message_start, got {event_types[0]!r}",
                event_index=0,
                event_type=event_types[0] or "(unknown)",
            )
        )

    # --- Rule 2: message_stop must be last ---
    if event_types[-1] != "message_stop":
        result.violations.append(
            StreamViolation(
                rule="message_stop_last",
                message=f"Last event must be message_stop, got {event_types[-1]!r}",
                event_index=len(events) - 1,
                event_type=event_types[-1] or "(unknown)",
            )
        )

    # --- Rule 3: All content_block_* events must precede message_delta ---
    message_delta_idx = None
    for i, t in enumerate(event_types):
        if t == "message_delta":
            message_delta_idx = i
            break

    if message_delta_idx is not None:
        for i, t in enumerate(event_types):
            if t in _CONTENT_BLOCK_EVENTS and i > message_delta_idx:
                result.violations.append(
                    StreamViolation(
                        rule="content_before_message_delta",
                        message=(
                            f"Content block event at position {i} "
                            f"appears after message_delta at position {message_delta_idx}"
                        ),
                        event_index=i,
                        event_type=t,
                    )
                )

    # --- Rule 4: Block lifecycle (start → delta(s) → stop) ---
    # Track which blocks have been started and stopped
    started_blocks: set[int] = set()
    stopped_blocks: set[int] = set()
    highest_start_index = -1

    for i, (event, t) in enumerate(zip(events, event_types)):
        if t not in _CONTENT_BLOCK_EVENTS:
            continue

        idx = _get_block_index(event)
        if idx is None:
            result.violations.append(
                StreamViolation(
                    rule="block_index_present",
                    message="Content block event missing index field",
                    event_index=i,
                    event_type=t or "(unknown)",
                )
            )
            continue

        if t == "content_block_start":
            # Rule 5: Block indices must be non-negative and monotonically increasing for starts
            if idx < 0:
                result.violations.append(
                    StreamViolation(
                        rule="block_index_non_negative",
                        message=f"Block index {idx} is negative",
                        event_index=i,
                        event_type=t,
                    )
                )
            if idx <= highest_start_index:
                result.violations.append(
                    StreamViolation(
                        rule="block_start_monotonic",
                        message=(
                            f"Block start index {idx} is not greater than previous start index {highest_start_index}"
                        ),
                        event_index=i,
                        event_type=t,
                    )
                )
            if idx >= 0:
                highest_start_index = idx
            started_blocks.add(idx)

        elif t == "content_block_delta":
            # Rule 6: No delta without a preceding start
            if idx not in started_blocks:
                result.violations.append(
                    StreamViolation(
                        rule="delta_after_start",
                        message=f"content_block_delta for index {idx} without preceding start",
                        event_index=i,
                        event_type=t,
                    )
                )
            # No delta after stop
            if idx in stopped_blocks:
                result.violations.append(
                    StreamViolation(
                        rule="delta_before_stop",
                        message=f"content_block_delta for index {idx} after it was already stopped",
                        event_index=i,
                        event_type=t,
                    )
                )

        elif t == "content_block_stop":
            if idx not in started_blocks:
                result.violations.append(
                    StreamViolation(
                        rule="stop_after_start",
                        message=f"content_block_stop for index {idx} without preceding start",
                        event_index=i,
                        event_type=t,
                    )
                )
            if idx in stopped_blocks:
                result.violations.append(
                    StreamViolation(
                        rule="block_stopped_once",
                        message=f"content_block_stop for index {idx} but block was already stopped",
                        event_index=i,
                        event_type=t,
                    )
                )
            stopped_blocks.add(idx)

    # All started blocks should be stopped (before message_delta)
    unclosed = started_blocks - stopped_blocks
    if unclosed:
        result.violations.append(
            StreamViolation(
                rule="blocks_closed",
                message=f"Content blocks started but never stopped: {sorted(unclosed)}",
                event_index=len(events) - 1,
                event_type="(end of stream)",
            )
        )

    return result
