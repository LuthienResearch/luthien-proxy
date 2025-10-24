"""StreamState for tracking aggregated streaming response state.

Passed to policy callbacks on each chunk with block-level aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from luthien_proxy.v2.streaming.stream_blocks import StreamBlock


@dataclass
class StreamState:
    """Complete state of a streaming response.

    Passed to policy callback on each chunk, providing:
    - All blocks that have been started (completed and in-progress)
    - Current block being streamed
    - Block that just completed (if any)
    - Overall finish reason when stream ends

    Blocks stream sequentially: content (if any) → tool calls (if any) → finish.
    At most one block completes per chunk.
    """

    blocks: list[StreamBlock] = field(default_factory=list)
    """All blocks in sequential order (completed + in-progress)."""

    current_block: Optional[StreamBlock] = None
    """Block currently being streamed (None before first block starts)."""

    just_completed: Optional[StreamBlock] = None
    """Block that completed in this chunk (None if no completion).

    This field is set when a block transitions from in-progress to complete.
    It is cleared before processing the next chunk.
    """

    finish_reason: Optional[str] = None
    """Overall stream completion reason when set.

    Values: "stop" (normal), "tool_calls" (ended with tools), "length" (max tokens).
    None while streaming is in progress.
    """


__all__ = ["StreamState"]
