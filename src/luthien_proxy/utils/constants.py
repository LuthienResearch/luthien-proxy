"""Shared constants used across proxy and control plane components."""

from __future__ import annotations

# Maximum number of characters to include when logging content previews.
CONTENT_PREVIEW_MAX_LENGTH = 50

# Smallest interval (in seconds) between control-plane poll attempts in the stream
# orchestrator. Using a floor avoids busy loops while still keeping latency low.
MIN_STREAM_POLL_INTERVAL_SECONDS = 0.01

# ------------------------------------------------------------------------------
# Streaming Pipeline Queue/Buffer Sizes
# ------------------------------------------------------------------------------

# Maximum queue size for async queues in the policy orchestrator.
# Acts as a circuit breaker on overflow to prevent unbounded memory growth.
DEFAULT_QUEUE_SIZE = 10000

# Maximum number of chunks to buffer in the transaction recorder before truncation.
# Prevents memory exhaustion on very long streaming responses.
DEFAULT_MAX_CHUNKS_QUEUED = 4096

# ------------------------------------------------------------------------------
# Logging Truncation Lengths
# ------------------------------------------------------------------------------

# Maximum characters for backend chunk logging (DEBUG level).
LOG_CHUNK_TRUNCATION_LENGTH = 300

# Maximum characters for SSE event logging (DEBUG level).
LOG_SSE_EVENT_TRUNCATION_LENGTH = 200

# ------------------------------------------------------------------------------
# LLM Judge Defaults
# ------------------------------------------------------------------------------

# Default max tokens for judge LLM responses.
DEFAULT_JUDGE_MAX_TOKENS = 256

# Default max tokens for LLM requests when not specified.
DEFAULT_LLM_MAX_TOKENS = 1024
