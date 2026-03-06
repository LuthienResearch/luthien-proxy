"""In-memory usage counters with atomic snapshot-and-reset.

Thread safety: all methods use a threading lock because counters may be
incremented from async tasks running on different threads (e.g. streaming
finalizers). The lock is never held for I/O so contention is negligible.
"""

from __future__ import annotations

import threading
from typing import TypedDict


class MetricsSnapshot(TypedDict):
    """Snapshot of usage metrics for a single rollup interval."""

    requests_accepted: int
    requests_completed: int
    input_tokens: int
    output_tokens: int
    streaming_requests: int
    non_streaming_requests: int
    sessions_with_ids: int


class UsageCollector:
    """Collects aggregate usage metrics in memory."""

    def __init__(self) -> None:
        """Initialize with zeroed counters."""
        self._lock = threading.Lock()
        self._requests_accepted = 0
        self._requests_completed = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._streaming_requests = 0
        self._non_streaming_requests = 0
        self._session_ids: set[str] = set()

    def record_accepted(self) -> None:
        """Record that a request was accepted into the pipeline."""
        with self._lock:
            self._requests_accepted += 1

    def record_completed(self, *, is_streaming: bool) -> None:
        """Record that a request completed successfully."""
        with self._lock:
            self._requests_completed += 1
            if is_streaming:
                self._streaming_requests += 1
            else:
                self._non_streaming_requests += 1

    def record_tokens(self, *, input_tokens: int, output_tokens: int) -> None:
        """Record token usage (Anthropic path only)."""
        with self._lock:
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens

    def record_session(self, session_id: str | None) -> None:
        """Record a session ID if present."""
        if session_id is None:
            return
        with self._lock:
            self._session_ids.add(session_id)

    def snapshot_and_reset(self) -> MetricsSnapshot:
        """Take a snapshot of current counters and reset them to zero."""
        with self._lock:
            snapshot = MetricsSnapshot(
                requests_accepted=self._requests_accepted,
                requests_completed=self._requests_completed,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                streaming_requests=self._streaming_requests,
                non_streaming_requests=self._non_streaming_requests,
                sessions_with_ids=len(self._session_ids),
            )
            self._requests_accepted = 0
            self._requests_completed = 0
            self._input_tokens = 0
            self._output_tokens = 0
            self._streaming_requests = 0
            self._non_streaming_requests = 0
            self._session_ids = set()
            return snapshot
