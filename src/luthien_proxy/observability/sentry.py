"""Sentry SDK integration — initialization and two-layer data scrubbing.

Layer 1 (EventScrubber): strips values by key name (api_key, token, etc.)
Layer 2 (before_send hook): summarizes LLM content variables with type+length,
strips cookies/server_name, redacts non-safe headers.
"""

from __future__ import annotations

import logging
from itertools import islice
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Event, Hint

from luthien_proxy.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scrubbing constants and helpers (always importable for tests)
# ---------------------------------------------------------------------------

# IMPORTANT: When adding new local variables that carry LLM content (prompts,
# messages, responses) to pipeline code, add their names here so Sentry
# summarizes them instead of capturing the raw content.
# See dev/context/sentry.md for the full scrubbing design.
_LLM_CONTENT_VARS = {
    "body",
    "messages",
    "prompt",
    "request_message",
    "final_request",
    "final_request_dict",
    "anthropic_request",
    "initial_request",
    "backend_response",
    "final_response",
    "emitted",
    "accumulated_events",
    "raw_http_request",
}

_SAFE_REQUEST_KEYS = {"model", "stream", "max_tokens", "temperature", "top_p", "top_k"}
_SAFE_HEADERS = {"content-type", "accept", "user-agent", "x-request-id"}

_EXTRA_DENYLIST: list[str] = [
    "anthropic_api_key",
    "openai_api_key",
    "proxy_api_key",
    "admin_api_key",
    "resolved_api_key",
    "explicit_key",
    "bearer_token",
    "api_key_header",
]


def _summarize(value: Any) -> Any:
    """Replace a value with its type and size, preserving debuggability."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return f"<str len={len(value)}>"
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"
    if isinstance(value, list):
        return f"<list len={len(value)}>"
    if isinstance(value, dict):
        keys = list(islice(value.keys(), 8))
        suffix = ", ..." if len(value) > 8 else ""
        return f"<dict keys={keys}{suffix}>"
    return f"<{type(value).__name__}>"


def _sentry_before_send(event: Event, hint: Hint) -> Event | None:
    """Selectively redact sensitive data while preserving debugging context.

    Keeps variable names, types, and safe values (call_id, model, chunk_count).
    Strips: LLM content values, request bodies (keeps keys), cookies.
    The built-in EventScrubber handles API key/token/auth scrubbing by key name.

    Mutates event in-place per Sentry's before_send contract. Return None to
    drop the event entirely, or the (mutated) event to send it.
    """
    exc_info = hint.get("exc_info")
    if isinstance(exc_info, tuple) and exc_info[0] in {KeyboardInterrupt, SystemExit}:
        return None

    event.pop("server_name", None)

    request = event.get("request", {})
    request.pop("cookies", None)
    if "headers" in request and isinstance(request["headers"], dict):
        request["headers"] = {
            k: v if k.lower() in _SAFE_HEADERS else "[REDACTED]" for k, v in request["headers"].items()
        }
    if "data" in request:
        if isinstance(request["data"], dict):
            request["data"] = {k: v if k in _SAFE_REQUEST_KEYS else _summarize(v) for k, v in request["data"].items()}
        elif isinstance(request["data"], (str, list)):
            request["data"] = _summarize(request["data"])

    for exc_entry in event.get("exception", {}).get("values", []):
        for frame in exc_entry.get("stacktrace", {}).get("frames", []):
            frame_vars = frame.get("vars")
            if not frame_vars:
                continue
            for var_name in list(frame_vars.keys()):
                if var_name in _LLM_CONTENT_VARS:
                    frame_vars[var_name] = _summarize(frame_vars[var_name])

    return event


def init_sentry(settings: Settings | None = None) -> None:
    """Initialize Sentry SDK if enabled. No-op when disabled or DSN is missing."""
    if settings is None:
        settings = get_settings()

    if not settings.sentry_enabled:
        return

    if not settings.sentry_dsn:
        logger.warning("SENTRY_ENABLED=true but SENTRY_DSN is empty — Sentry is NOT active")
        return

    # OTel exporter logs at ERROR when Tempo is unreachable — expected in
    # local dev without Docker. Don't let these burn Sentry quota.
    ignore_logger("opentelemetry.sdk.trace.export")

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        send_default_pii=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        environment=settings.environment,
        release=f"{settings.service_name}@{settings.service_version}",
        server_name=settings.sentry_server_name or None,
        before_send=_sentry_before_send,
        in_app_include=["luthien_proxy"],
        event_scrubber=EventScrubber(denylist=DEFAULT_DENYLIST + _EXTRA_DENYLIST),
    )
    logger.info("Sentry initialized (env=%s)", settings.environment)
