"""Sentry SDK integration — initialization and two-layer data scrubbing.

Layer 1 (EventScrubber): strips values by key name (api_key, token, etc.)
Layer 2 (before_send hook): summarizes LLM content variables with type+length,
strips cookies/server_name, redacts non-safe headers, and drops expected
upstream provider errors.
"""

from __future__ import annotations

import logging
from itertools import islice
from typing import Any, Mapping

import sentry_sdk
from anthropic import APIStatusError
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

# Upstream statuses that mean the request/response is the client's or the
# provider's problem, not a proxy defect. The pipeline already converts every
# one of these into a BackendAPIError response for the client (see
# _handle_anthropic_error / _build_error_event, which log at warning and
# handle every AnthropicStatusError the same way regardless of status code)
# and, for the throttling/availability codes, the caller retries. They arrive
# here anyway because the Sentry Anthropic integration captures at the SDK
# call site with handled=false, before our handler ever sees them.

# 408/429/500/502/503/504/529: provider throttling or brief unavailability.
# Structurally impossible for the proxy to have provoked — dropped
# unconditionally (56 unhandled 429 events in three days before this filter
# existed).
_PROVIDER_SIDE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})

# 400/404: the client sent content Anthropic legitimately rejected —
# malformed message content or an unknown model name (LUTHIEN-6: 1,080
# events for one recurring 400; LUTHIEN-2: unknown-model 404s). This is a
# property of the request body alone, independent of which credential
# reached Anthropic. But the proxy is not always a transparent passthrough:
# policy hooks can mutate or replace the request before it reaches
# Anthropic, and operator-configured UPSTREAM_HEADERS or policy-context
# injection can alter it too. Only drop these when the request carries
# provenance (the PASSTHROUGH_TAG scope tag, set at the actual upstream
# call boundary in _AnthropicPolicyIO) proving nothing touched it after the
# client sent it.
_CONTENT_DEPENDENT_STATUS_CODES = frozenset({400, 404})

# 401: an invalid bearer token passed through client-credential mode
# (LUTHIEN-D). Unlike 400/404, this is NOT solely a body/header property: in
# client-key auth mode the *credential* forwarded upstream is the operator's
# own ANTHROPIC_API_KEY rather than anything the client sent, so an
# unmodified body proves nothing about whose credential caused the 401 in
# that mode — an invalid operator credential must still report. Dropping a
# 401 requires BOTH the PASSTHROUGH_TAG (request untouched) AND the
# CREDENTIAL_PASSTHROUGH_TAG (credential is the client's own, not the
# operator's shared key).
_CREDENTIAL_DEPENDENT_STATUS_CODES = frozenset({401})

_CLIENT_OR_PASSTHROUGH_STATUS_CODES = _CONTENT_DEPENDENT_STATUS_CODES | _CREDENTIAL_DEPENDENT_STATUS_CODES

_EXPECTED_UPSTREAM_STATUS_CODES = _PROVIDER_SIDE_STATUS_CODES | _CLIENT_OR_PASSTHROUGH_STATUS_CODES

# Scope tag set by the Anthropic pipeline (see _AnthropicPolicyIO in
# anthropic_processor.py) immediately before the upstream call, true only
# when the request body and headers going to Anthropic are exactly what the
# client sent — no policy hook, header injection, or context injection
# touched them. Says nothing about which credential was forwarded; see
# CREDENTIAL_PASSTHROUGH_TAG for that.
PASSTHROUGH_TAG = "luthien.request_unmodified_passthrough"

# Scope tag set alongside PASSTHROUGH_TAG, true only when the credential
# forwarded to Anthropic is the client's own (passthrough / BOTH / explicit
# x-anthropic-api-key auth) rather than the operator's shared
# ANTHROPIC_API_KEY substituted in client-key auth mode. Only 401 needs
# this — a bad body/model name (400/404) is credential-independent.
CREDENTIAL_PASSTHROUGH_TAG = "luthien.credential_client_supplied"

# The decision table itself: which scope tags (all must be True) are required
# to drop each expected-upstream status code. Empty for the provider-side
# codes (unconditional), PASSTHROUGH_TAG alone for the content-dependent
# codes, both tags for the credential-dependent code. Built from the sets
# above so it cannot drift from the per-category documentation there, and a
# status absent from every set above is absent here too, so it always
# reports — see _is_expected_upstream_error.
_REQUIRED_TAGS_BY_STATUS: dict[int, tuple[str, ...]] = {
    **dict.fromkeys(_PROVIDER_SIDE_STATUS_CODES, ()),
    **dict.fromkeys(_CONTENT_DEPENDENT_STATUS_CODES, (PASSTHROUGH_TAG,)),
    **dict.fromkeys(_CREDENTIAL_DEPENDENT_STATUS_CODES, (PASSTHROUGH_TAG, CREDENTIAL_PASSTHROUGH_TAG)),
}


def tag_request_provenance(unmodified: bool) -> None:
    """Record on the current Sentry scope whether the outgoing request is untouched.

    Called at the upstream call boundary so `_sentry_before_send` can tell a
    genuine client/provider 400/404 from one the proxy or a policy caused.
    Safe to call even when Sentry is disabled or uninitialized —
    `sentry_sdk.set_tag` is a no-op against the default scope in that case.
    """
    sentry_sdk.set_tag(PASSTHROUGH_TAG, unmodified)


def tag_credential_provenance(client_supplied: bool) -> None:
    """Record on the current Sentry scope whether the forwarded credential is the client's own.

    Called at the upstream call boundary alongside `tag_request_provenance`
    so `_sentry_before_send` can tell a genuine client credential failure
    (401) from an invalid operator credential in client-key auth mode. Safe
    to call even when Sentry is disabled or uninitialized —
    `sentry_sdk.set_tag` is a no-op against the default scope in that case.
    """
    sentry_sdk.set_tag(CREDENTIAL_PASSTHROUGH_TAG, client_supplied)


_SAFE_REQUEST_KEYS = {"model", "stream", "max_tokens", "temperature", "top_p", "top_k"}
_SAFE_HEADERS = {"content-type", "accept", "user-agent", "x-request-id"}

_EXTRA_DENYLIST: list[str] = [
    "anthropic_api_key",
    "openai_api_key",
    "client_api_key",
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


def _is_expected_upstream_error(exc: BaseException | None, tags: Mapping[str, object]) -> bool:
    """True for provider errors that are the client's or provider's fault, not ours.

    Matches on the SDK exception's own status_code rather than its class so a
    provider SDK renaming or adding a status subclass cannot silently start
    reporting again. Looks up the required scope tags for that status in
    _REQUIRED_TAGS_BY_STATUS and drops only when every one of them is True;
    a status with no entry there always reports.
    """
    if not isinstance(exc, APIStatusError):
        return False
    required_tags = _REQUIRED_TAGS_BY_STATUS.get(exc.status_code)
    if required_tags is None:
        return False
    return all(tags.get(tag) is True for tag in required_tags)


def _sentry_before_send(event: Event, hint: Hint) -> Event | None:
    """Selectively redact sensitive data while preserving debugging context.

    Keeps variable names, types, and safe values (call_id, model, chunk_count).
    Strips: LLM content values, request bodies (keeps keys), cookies.
    The built-in EventScrubber handles API key/token/auth scrubbing by key name.

    Mutates event in-place per Sentry's before_send contract. Return None to
    drop the event entirely, or the (mutated) event to send it.
    """
    exc_info = hint.get("exc_info")
    if isinstance(exc_info, tuple):
        if exc_info[0] in {KeyboardInterrupt, SystemExit}:
            return None
        if _is_expected_upstream_error(exc_info[1], event.get("tags") or {}):
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

    if not settings.sentry_dsn.startswith(("https://", "http://")):
        logger.warning(
            "SENTRY_ENABLED=true but SENTRY_DSN=%r is not a valid URL — Sentry is NOT active",
            settings.sentry_dsn,
        )
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
