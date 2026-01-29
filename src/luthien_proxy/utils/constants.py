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

# Maximum characters for tool call arguments in logs/error messages.
TOOL_ARGS_TRUNCATION_LENGTH = 200

# Maximum characters for database URL preview in startup logs.
DB_URL_PREVIEW_LENGTH = 20

# ------------------------------------------------------------------------------
# LLM Judge Defaults
# ------------------------------------------------------------------------------

# Default max tokens for judge LLM responses.
DEFAULT_JUDGE_MAX_TOKENS = 256

# Default max tokens for LLM requests when not specified.
DEFAULT_LLM_MAX_TOKENS = 1024

# ------------------------------------------------------------------------------
# Queue/Stream Timeouts
# ------------------------------------------------------------------------------

# Timeout (in seconds) for putting items in async queues to prevent deadlock.
# Used when downstream handlers (policy executor, client formatters) are slow/stalled.
QUEUE_PUT_TIMEOUT_SECONDS = 30.0

# ------------------------------------------------------------------------------
# Request Limits
# ------------------------------------------------------------------------------

# Maximum HTTP request payload size (10 MB).
# Prevents OOM attacks and limits request complexity.
MAX_REQUEST_PAYLOAD_BYTES = 10_485_760

# ------------------------------------------------------------------------------
# Debug/Admin Endpoint Defaults
# ------------------------------------------------------------------------------

# Default number of recent calls to return in debug list endpoint.
DEBUG_CALLS_DEFAULT_LIMIT = 50

# Maximum number of recent calls allowed in debug list endpoint.
DEBUG_CALLS_MAX_LIMIT = 1000

# ------------------------------------------------------------------------------
# Event Stream Configuration
# ------------------------------------------------------------------------------

# Interval (in seconds) between SSE keepalive heartbeat events.
# Prevents client disconnections due to inactivity.
HEARTBEAT_INTERVAL_SECONDS = 15.0

# Timeout (in seconds) for Redis pub/sub polling.
REDIS_PUBSUB_TIMEOUT_SECONDS = 1.0

# Additional buffer (in seconds) added to asyncio timeout for pub/sub operations.
# Accounts for asyncio scheduling overhead.
REDIS_POLL_TIMEOUT_BUFFER_SECONDS = 0.5

# ------------------------------------------------------------------------------
# Security & Hashing
# ------------------------------------------------------------------------------

# Length of API key hash to display in logs (SHA256 substring).
# Balance between security and debugging convenience.
API_KEY_HASH_LENGTH = 16

# ------------------------------------------------------------------------------
# Redis Configuration
# ------------------------------------------------------------------------------

# Timeout (in seconds) for acquiring Redis locks.
# Prevents indefinite blocking when lock contention occurs.
REDIS_LOCK_TIMEOUT_SECONDS = 10

# ------------------------------------------------------------------------------
# OpenTelemetry Configuration
# ------------------------------------------------------------------------------

# Length of trace ID in hex format (16 bytes = 32 hex chars).
# Used as placeholder when no OTEL context is available.
OTEL_TRACE_ID_HEX_LENGTH = 32

# Length of span ID in hex format (8 bytes = 16 hex chars).
# Used as placeholder when no OTEL context is available.
OTEL_SPAN_ID_HEX_LENGTH = 16

# ------------------------------------------------------------------------------
# Gateway Server
# ------------------------------------------------------------------------------

# Default port for gateway HTTP server.
DEFAULT_GATEWAY_PORT = 8000

# ------------------------------------------------------------------------------
# History Viewer
# ------------------------------------------------------------------------------

# Default number of sessions to return in history list endpoint.
HISTORY_SESSIONS_DEFAULT_LIMIT = 50

# Maximum number of sessions allowed in history list endpoint.
HISTORY_SESSIONS_MAX_LIMIT = 10000
