"""Actionable advice for errors surfaced to proxy clients.

Raw upstream API errors are often opaque to end users (e.g. a bare
``"messages.0.bogus: Extra inputs are not permitted"``). This module maps
known error shapes to short, actionable suggestions that the pipeline
appends to the client-facing error message.

Design invariant: the raw upstream message is always preserved. Advice is
appended after the original text, never substituted for it, so clients and
operators that rely on the exact upstream wording lose nothing.
"""

from __future__ import annotations

import re

SUGGESTION_PREFIX = "Suggestion:"

# Advice for errors that never come from the upstream API's HTTP layer.
CONNECTION_ERROR_ADVICE = (
    "The Luthien proxy could not reach the upstream API. Check the proxy host's "
    "network connection and any custom base URL configuration, then retry."
)
CREDENTIAL_ERROR_ADVICE = (
    "The proxy's backend credentials could not be resolved. Check the API key or "
    "OAuth token configured for the Luthien proxy, or contact your Luthien proxy administrator."
)
INTERNAL_ERROR_ADVICE = (
    "This error occurred inside the Luthien proxy, not the upstream API. Retry once; "
    "if it persists, ask your Luthien proxy administrator to check the proxy logs."
)

_GENERIC_ADVICE = (
    "Retry the request; if the error persists, ask your Luthien proxy administrator to check the proxy logs."
)

_INVALID_REQUEST_ADVICE = (
    "The upstream API rejected this request as invalid. Fix the field named in the message above and resend."
)

# Ordered rules: (status_code, message pattern or None, advice).
# First match wins. A None pattern is the fallback for that status code,
# so pattern-specific rules must come before their status fallback.
_ADVICE_RULES: tuple[tuple[int, re.Pattern[str] | None, str], ...] = (
    (
        400,
        re.compile(r"Extra inputs are not permitted", re.IGNORECASE),
        "The API rejected a field it does not recognize (named just before "
        "'Extra inputs are not permitted'). Remove that field from the request and resend.",
    ),
    (
        400,
        re.compile(r"max_tokens", re.IGNORECASE),
        "Check the max_tokens value: it must be a positive integer within the "
        "selected model's output limit. Lower it and resend.",
    ),
    (
        400,
        re.compile(r"credit balance", re.IGNORECASE),
        "The Anthropic account behind this proxy has run out of credits. Add credits "
        "in the Anthropic Console billing page, or contact your Luthien proxy administrator.",
    ),
    (400, None, _INVALID_REQUEST_ADVICE),
    (
        401,
        None,
        "The upstream API rejected the credentials. If you supply your own API key "
        "through the proxy, verify it is valid and active. If the proxy operator manages "
        "credentials, contact your Luthien proxy administrator.",
    ),
    (
        403,
        None,
        "The credentials are valid but not allowed to perform this action. Confirm your "
        "account or workspace has access to the requested model or feature.",
    ),
    (
        404,
        re.compile(r"model", re.IGNORECASE),
        "The requested model was not found. Check the model name for typos and confirm your account has access to it.",
    ),
    (
        404,
        None,
        "The requested resource was not found. Check the request path and any identifiers.",
    ),
    (
        413,
        None,
        "The request payload is too large. Trim conversation history or large content blocks and resend.",
    ),
    (422, None, _INVALID_REQUEST_ADVICE),
    (
        429,
        None,
        "The upstream API rate limit was hit. Wait briefly and retry with backoff. If this "
        "happens often, ask your Luthien proxy administrator about rate limits.",
    ),
    (
        500,
        None,
        "The upstream API hit an internal error. This is usually transient: retry the "
        "request, and check the provider's status page if it persists.",
    ),
    (
        503,
        None,
        "The upstream API is temporarily unavailable. Retry with exponential backoff.",
    ),
    (
        529,
        None,
        "The upstream API is temporarily overloaded. Retry with exponential backoff.",
    ),
)


def get_error_advice(status_code: int | None, message: str) -> str:
    """Return a short actionable suggestion for an upstream error.

    Args:
        status_code: HTTP status code from the upstream API, if known.
        message: Raw upstream error message (used for pattern-specific advice).

    Returns:
        A human-readable suggestion. Falls back to generic retry guidance when
        no specific rule matches, so callers can rely on always getting advice.
    """
    for rule_status, pattern, advice in _ADVICE_RULES:
        if status_code != rule_status:
            continue
        if pattern is None or pattern.search(message):
            return advice
    return _GENERIC_ADVICE


def append_advice(message: str, advice: str | None) -> str:
    """Append a suggestion to an error message, preserving the original text.

    Returns the message unchanged when advice is None or empty, or when the
    message already carries a suggestion (guards against double-appending if
    an error is formatted twice on its way out).
    """
    if not advice:
        return message
    if SUGGESTION_PREFIX in message:
        return message
    return f"{message}\n\n{SUGGESTION_PREFIX} {advice}"


__all__ = [
    "CONNECTION_ERROR_ADVICE",
    "CREDENTIAL_ERROR_ADVICE",
    "INTERNAL_ERROR_ADVICE",
    "SUGGESTION_PREFIX",
    "append_advice",
    "get_error_advice",
]
