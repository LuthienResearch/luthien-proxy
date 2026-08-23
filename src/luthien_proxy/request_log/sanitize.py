"""Sanitize sensitive data from HTTP headers before storage."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Headers whose values should be fully redacted
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "x-anthropic-api-key",
        "x-goog-api-key",
        "proxy-authorization",
        "cookie",
        "set-cookie",
    }
)

# Patterns that look like API keys/tokens in header values
_SECRET_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9]{8,})"  # OpenAI-style keys
    r"|(sk-ant-[a-zA-Z0-9]{8,})"  # Anthropic-style keys
    r"|([a-f0-9]{32,})",  # Long hex strings (generic tokens)
    re.IGNORECASE,
)


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove sensitive values from HTTP headers.

    Known sensitive headers (Authorization, x-api-key, etc.) have their
    values fully replaced with '[REDACTED]'. Other headers are checked
    for patterns that look like API keys and partially masked.

    Args:
        headers: Raw HTTP headers (lowercase keys expected).

    Returns:
        New dict with sensitive values redacted.
    """
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        lower_key = key.lower()
        if lower_key in _SENSITIVE_HEADERS:
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = _SECRET_PATTERN.sub("[REDACTED]", value)
    return sanitized


def sanitize_url(url: str) -> str:
    """Redact API keys embedded in query parameters before request log storage."""
    parts = urlsplit(url)
    query = urlencode(
        [
            (key, "[REDACTED]" if key.lower() == "key" else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


__all__ = ["sanitize_headers", "sanitize_url"]
