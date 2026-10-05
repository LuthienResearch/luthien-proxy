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


class StreamingProtocolValidator:
    """Incremental Anthropic streaming protocol validator.

    Feed events one at a time via ``observe()``; each call returns the
    violations introduced by that event, BEFORE the caller forwards it.
    This is what enables mid-stream enforcement: the pipeline can detect a
    corrupted outbound stream and emit a clean client error event instead of
    forwarding the corrupting event (the PR #356 session-bricking class).

    Call ``finalize()`` after the last event for the end-of-stream rules
    (stream non-empty, message_stop last, all blocks closed). Those rules are
    only decidable once the stream has ended, so they cannot gate forwarding.

    ``validate_anthropic_event_ordering()`` is the batch wrapper over this
    class; both share the same rule definitions.
    """

    def __init__(self) -> None:
        """Initialize validator state for a fresh stream."""
        self._event_count = 0
        self._message_delta_index: int | None = None
        self._started_blocks: set[int] = set()
        self._stopped_blocks: set[int] = set()
        self._highest_start_index = -1
        self._last_event_type: str | None = None
        self._last_event_index = -1

    def observe(self, event: dict | object) -> list[StreamViolation]:
        """Record one event and return any violations it introduces.

        Args:
            event: Event dict or Pydantic model with a ``type`` field/attribute.

        Returns:
            Violations detectable at this event (empty list if the event is
            protocol-conformant so far).
        """
        i = self._event_count
        self._event_count += 1
        t = _get_event_type(event)
        self._last_event_type = t
        self._last_event_index = i

        violations: list[StreamViolation] = []

        # --- Rule 1: message_start must be first ---
        if i == 0 and t != "message_start":
            violations.append(
                StreamViolation(
                    rule="message_start_first",
                    message=f"First event must be message_start, got {t!r}",
                    event_index=0,
                    event_type=t or "(unknown)",
                )
            )

        if t == "message_delta" and self._message_delta_index is None:
            self._message_delta_index = i

        if t not in _CONTENT_BLOCK_EVENTS:
            return violations

        # --- Rule 3: All content_block_* events must precede message_delta ---
        if self._message_delta_index is not None and i > self._message_delta_index:
            violations.append(
                StreamViolation(
                    rule="content_before_message_delta",
                    message=(
                        f"Content block event at position {i} "
                        f"appears after message_delta at position {self._message_delta_index}"
                    ),
                    event_index=i,
                    event_type=t,
                )
            )

        # --- Rule 4: Block lifecycle (start → delta(s) → stop) ---
        idx = _get_block_index(event)
        if idx is None:
            violations.append(
                StreamViolation(
                    rule="block_index_present",
                    message="Content block event missing index field",
                    event_index=i,
                    event_type=t or "(unknown)",
                )
            )
            return violations

        if t == "content_block_start":
            # Rule 5: Block indices must be non-negative and monotonically increasing for starts
            if idx < 0:
                violations.append(
                    StreamViolation(
                        rule="block_index_non_negative",
                        message=f"Block index {idx} is negative",
                        event_index=i,
                        event_type=t,
                    )
                )
            if idx <= self._highest_start_index:
                violations.append(
                    StreamViolation(
                        rule="block_start_monotonic",
                        message=(
                            f"Block start index {idx} is not greater than "
                            f"previous start index {self._highest_start_index}"
                        ),
                        event_index=i,
                        event_type=t,
                    )
                )
            if idx >= 0:
                self._highest_start_index = idx
            self._started_blocks.add(idx)

        elif t == "content_block_delta":
            # Rule 6: No delta without a preceding start
            if idx not in self._started_blocks:
                violations.append(
                    StreamViolation(
                        rule="delta_after_start",
                        message=f"content_block_delta for index {idx} without preceding start",
                        event_index=i,
                        event_type=t,
                    )
                )
            # No delta after stop
            if idx in self._stopped_blocks:
                violations.append(
                    StreamViolation(
                        rule="delta_before_stop",
                        message=f"content_block_delta for index {idx} after it was already stopped",
                        event_index=i,
                        event_type=t,
                    )
                )

        elif t == "content_block_stop":
            if idx not in self._started_blocks:
                violations.append(
                    StreamViolation(
                        rule="stop_after_start",
                        message=f"content_block_stop for index {idx} without preceding start",
                        event_index=i,
                        event_type=t,
                    )
                )
            if idx in self._stopped_blocks:
                violations.append(
                    StreamViolation(
                        rule="block_stopped_once",
                        message=f"content_block_stop for index {idx} but block was already stopped",
                        event_index=i,
                        event_type=t,
                    )
                )
            self._stopped_blocks.add(idx)

        return violations

    def finalize(self) -> list[StreamViolation]:
        """Return violations only decidable at end of stream.

        Rules: stream non-empty, message_stop last, all started blocks stopped.

        Only well-defined for streams that were intended to complete. A stream
        aborted mid-flight (e.g. after a protocol violation) will always fail
        these completeness rules; callers should skip finalize() for aborted
        streams to avoid double-reporting the same failure.
        """
        if self._event_count == 0:
            return [
                StreamViolation(
                    rule="non_empty",
                    message="Event stream is empty",
                    event_index=-1,
                    event_type="(none)",
                )
            ]

        violations: list[StreamViolation] = []

        # --- Rule 2: message_stop must be last ---
        if self._last_event_type != "message_stop":
            violations.append(
                StreamViolation(
                    rule="message_stop_last",
                    message=f"Last event must be message_stop, got {self._last_event_type!r}",
                    event_index=self._last_event_index,
                    event_type=self._last_event_type or "(unknown)",
                )
            )

        # All started blocks should be stopped (before message_delta)
        unclosed = self._started_blocks - self._stopped_blocks
        if unclosed:
            violations.append(
                StreamViolation(
                    rule="blocks_closed",
                    message=f"Content blocks started but never stopped: {sorted(unclosed)}",
                    event_index=self._last_event_index,
                    event_type="(end of stream)",
                )
            )

        return violations


def validate_anthropic_event_ordering(
    events: list,
) -> StreamValidationResult:
    """Validate that a list of Anthropic streaming events follows protocol ordering.

    Batch wrapper over StreamingProtocolValidator: observes every event in
    order, then applies the end-of-stream rules.

    Args:
        events: List of event dicts or Pydantic model objects. Each must have
                a ``type`` field/attribute.

    Returns:
        StreamValidationResult with any violations found.
    """
    result = StreamValidationResult()
    validator = StreamingProtocolValidator()
    for event in events:
        result.violations.extend(validator.observe(event))
    result.violations.extend(validator.finalize())
    return result
