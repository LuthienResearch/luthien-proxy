"""URL sanitization utilities for safe logging."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def sanitize_url_for_logging(url: str) -> str:
    """Strip credentials from a URL so it can be safely logged.

    Replaces the password (and optionally username) portion of the URL
    with ``***`` while preserving host, port, path, and query parameters.

    Returns the original string unchanged if it cannot be parsed.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    if not parsed.password:
        return url

    # Rebuild netloc: keep username visible but mask the password
    if parsed.username:
        masked_netloc = f"{parsed.username}:***@{parsed.hostname}"
    else:
        masked_netloc = f"***@{parsed.hostname}"

    if parsed.port:
        masked_netloc += f":{parsed.port}"

    return urlunparse((parsed.scheme, masked_netloc, parsed.path, parsed.params, parsed.query, ""))
