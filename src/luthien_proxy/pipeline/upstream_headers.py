"""Configurable upstream header injection.

Reads header templates from the UPSTREAM_HEADERS environment variable (JSON)
and expands per-request context variables before forwarding to the backend.

Supported template variables::

    ${session_id}    — Claude Code session UUID (from metadata.user_id)
    ${request_path}  — HTTP request path (e.g. /v1/messages)
    ${env.VARNAME}   — Any environment variable

Example::

    UPSTREAM_HEADERS = '{"Helicone-Auth":"Bearer ${env.HELICONE_API_KEY}","Helicone-Session-Id":"${session_id}"}'

Design notes:

    **Config system bypass (intentional):** This feature reads from the
    ``UPSTREAM_HEADERS`` env var directly via ``os.environ`` rather than going
    through the ``config_fields.py`` / ``settings.py`` config system. The config
    system is designed for scalar values (str, int, bool) with per-field env
    vars. A JSON blob of arbitrary header templates doesn't fit that model
    cleanly. If this feature is later promoted to a first-class config field,
    the ``lru_cache`` on ``_load_header_templates()`` will need to be replaced
    with a config-registry-aware loader.

    **Restart required:** The ``lru_cache`` means UPSTREAM_HEADERS is read on
    the first request and cached for the process lifetime. Changes require a
    gateway restart.

Security:

    **Env var expansion surface:** The ``${env.VARNAME}`` syntax expands any
    environment variable into an outbound header value. This means anyone who
    can set the ``UPSTREAM_HEADERS`` value can route any env var (including
    ``ANTHROPIC_API_KEY``, ``ADMIN_API_KEY``, etc.) into upstream request
    headers. This is acceptable when the upstream is trusted (e.g., Helicone,
    Anthropic API) because server-side access is already required to set the
    env var. Do NOT use this feature to forward headers to untrusted upstreams.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.0-9A-Z^_`a-z|~]+")
_SENSITIVE_ENV_RE = re.compile(
    r"\$\{env\.([^}]*(?:KEY|SECRET|PASSWORD|TOKEN)[^}]*|DATABASE_URL|REDIS_URL)\}", re.IGNORECASE
)

# Hard-block: env vars that must never be forwarded to upstream, regardless of configuration.
# Checked at load time (startup) so misconfigurations fail fast rather than leaking credentials
# at request time.
_SENSITIVE_VAR_BLOCKLIST: frozenset[str] = frozenset(
    {
        "DATABASE_URL",
        "REDIS_URL",
        "ADMIN_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLIENT_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_APP_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_API_KEY",
        "AZURE_CLIENT_SECRET",
        "AZURE_STORAGE_KEY",
    }
)

_SENSITIVE_VAR_SUFFIXES: tuple[str, ...] = ("_KEY", "_SECRET", "_PASSWORD", "_TOKEN")

# Hard-block: HTTP headers that must never be overridden via UPSTREAM_HEADERS.
# These are reserved by the HTTP protocol or carry security-critical credentials
# that the gateway manages. Checked at load time (startup) so misconfigurations
# fail fast rather than silently corrupting requests at runtime.
_RESERVED_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "host",
        "x-api-key",
        "content-type",
        "content-length",
        "transfer-encoding",
    }
)


def _is_sensitive_var(name: str) -> bool:
    upper = name.upper()
    return upper in _SENSITIVE_VAR_BLOCKLIST or any(upper.endswith(s) for s in _SENSITIVE_VAR_SUFFIXES)


def _warn_sensitive_env_refs(templates: dict[str, str]) -> None:
    for header, template in templates.items():
        for match in _SENSITIVE_ENV_RE.finditer(template):
            logger.warning(
                "UPSTREAM_HEADERS: header %r references potentially sensitive env var ${env.%s} — "
                "ensure the upstream target is trusted",
                header,
                match.group(1),
            )


@lru_cache(maxsize=1)
def _load_header_templates() -> dict[str, str]:
    """Parse UPSTREAM_HEADERS env var once at first use.

    Raises ValueError at startup for reserved-header overrides or blocked env-var
    references (fail-fast). Returns empty dict if UPSTREAM_HEADERS is unset.
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
            if not _HEADER_NAME_RE.fullmatch(k):
                logger.warning("UPSTREAM_HEADERS: skipping invalid header name %r", k)
                continue
            if k.lower() in _RESERVED_HEADERS:
                raise ValueError(f"UPSTREAM_HEADERS: header '{k}' is a reserved header and cannot be overridden")
            result[k] = v
        if result:
            logger.info("Loaded %d upstream header template(s)", len(result))
        for header, template in result.items():
            for match in _TEMPLATE_PATTERN.finditer(template):
                var = match.group(1)
                if var.startswith("env."):
                    env_name = var[4:]
                    if _is_sensitive_var(env_name):
                        raise ValueError(
                            f"UPSTREAM_HEADERS: header '{header}' references blocked env var "
                            f"'${{env.{env_name}}}' — forwarding this variable is not permitted"
                        )
        _warn_sensitive_env_refs(result)
        return result
    except json.JSONDecodeError as e:
        logger.error("UPSTREAM_HEADERS: invalid JSON: %s", e)
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

    result = _TEMPLATE_PATTERN.sub(_replace, template)
    return result.replace("\r", "").replace("\n", "")


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
