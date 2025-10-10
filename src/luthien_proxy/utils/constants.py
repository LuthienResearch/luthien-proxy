"""Shared constants used across proxy and control plane components."""

from __future__ import annotations

# Maximum number of characters to include when logging content previews.
CONTENT_PREVIEW_MAX_LENGTH = 50

# Smallest interval (in seconds) between control-plane poll attempts in the stream
# orchestrator. Using a floor avoids busy loops while still keeping latency low.
MIN_STREAM_POLL_INTERVAL_SECONDS = 0.01
