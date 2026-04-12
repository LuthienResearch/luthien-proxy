"""Configurable upstream header injection.

Reads header templates from the UPSTREAM_HEADERS environment variable (JSON)
and expands per-request context variables before forwarding to the backend.

Supported template variables:
    ${session_id}    — Claude Code session UUID (from metadata.user_id)
    ${request_path}  — HTTP request path (e.g. /v1/messages)
    ${env.VARNAME}   — Any environment variable

Example:
    UPSTREAM_HEADERS='{"Helicone-Auth":"Bearer ${env.HELICONE_API_KEY}","Helicone-Session-Id":"${session_id}"}'
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")


@lru_cache(maxsize=1)
def _load_header_templates() -> dict[str, str]:
    """Parse UPSTREAM_HEADERS env var once at first use.

    Returns empty dict if unset or malformed (fail-open).
    """
    raw = os.environ.get("UPSTREAM_HEADERS", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            logger.warning("UPSTREAM_HEADERS must be a JSON object, got %s", type(parsed).__name__)
            return {}
        # Validate all values are strings
        result = {}
        for k, v in parsed.items():
            if not isinstance(k, str) or not isinstance(v, str):
                logger.warning("UPSTREAM_HEADERS: skipping non-string entry %r: %r", k, v)
                continue
            result[k] = v
        if result:
            logger.info("Loaded %d upstream header template(s)", len(result))
        return result
    except json.JSONDecodeError as e:
        logger.warning("UPSTREAM_HEADERS: invalid JSON: %s", e)
        return {}


def _expand_template(template: str, session_id: str | None, request_path: str) -> str:
    """Expand a single template string with context variables."""

    def _replace(match: re.Match[str]) -> str:
        var = match.group(1)
        if var == "session_id":
            return session_id or ""
        if var == "request_path":
            return request_path
        if var.startswith("env."):
            env_name = var[4:]
            return os.environ.get(env_name, "")
        logger.debug("Unknown template variable: ${%s}", var)
        return match.group(0)  # Leave unknown variables unexpanded

    return _TEMPLATE_PATTERN.sub(_replace, template)


def expand_upstream_headers(
    session_id: str | None,
    request_path: str,
) -> dict[str, str] | None:
    """Expand upstream header templates for a single request.

    Returns None if no upstream headers are configured (avoids unnecessary dict
    allocation on the hot path).
    """
    templates = _load_header_templates()
    if not templates:
        return None

    headers = {}
    for name, template in templates.items():
        value = _expand_template(template, session_id, request_path)
        # Skip headers that expand to empty (e.g. session_id not available)
        if value:
            headers[name] = value
    return headers or None
