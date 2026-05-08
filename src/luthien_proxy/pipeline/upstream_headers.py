"""Configurable upstream header injection.

Reads header templates from the ``UPSTREAM_HEADERS`` environment variable (a
JSON object: ``{header_name: template_string}``) and expands per-request
context variables before forwarding to the backend.

Supported template variables::

    ${session_id}    — Claude Code session UUID (from metadata.user_id)
    ${request_path}  — HTTP request path (e.g. /v1/messages)
    ${env.VARNAME}   — Any process environment variable

Example::

    UPSTREAM_HEADERS = '{"Helicone-Auth":"Bearer ${env.HELICONE_API_KEY}","Helicone-Session-Id":"${session_id}"}'

Trust model:

    The operator who runs the gateway sets ``UPSTREAM_HEADERS`` and the
    referenced env vars, and chooses the trusted upstream. The feature does
    not defend against hostile operators (they own the env), hostile clients
    (CRLF in ``session_id`` is malformed input, not an attack), or
    "exfiltration" of the operator's own secrets to a destination they
    configured. CRLF/NUL stripping and RFC 7230 token validation are input
    hygiene — they keep failures legible, not safer.

Validation:

    * Invalid JSON, a non-object root, or non-string entries raise at load
      time. Misconfiguration fails the gateway at startup, not at first
      request.
    * Header names must match the RFC 7230 token spec.
    * Hop-by-hop / framing headers (``Connection``, ``Content-Length``,
      ``Keep-Alive``, ``Proxy-Authenticate``, ``Proxy-Authorization``,
      ``Proxy-Connection``, ``TE``, ``Trailer``, ``Trailers``,
      ``Transfer-Encoding``, ``Upgrade``) are dropped with a warning —
      overriding them breaks HTTP transport.
    * Unknown template variables (``${foo}`` that isn't ``session_id``,
      ``request_path``, or ``env.X``) are logged at load time, not on every
      request.

Restart required:

    Template structure is parsed once and cached for the process lifetime,
    so changes to ``UPSTREAM_HEADERS`` itself require a gateway restart.
    Referenced env vars (``${env.X}``) are read per request — mutating them
    in the running process takes effect on the next request, but this is
    not a recommended workflow.

Config system bypass (intentional):

    This feature reads ``UPSTREAM_HEADERS`` directly via ``os.environ``
    rather than going through ``config_fields.py``. The config registry is
    typed for scalars; a JSON blob of arbitrary header templates does not
    fit that model. Operability comes from the startup audit log
    enumerating which env vars the templates reference.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")

# RFC 7230 token: 1*tchar where tchar = ALPHA / DIGIT / "!#$%&'*+-.^_`|~"
_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")

# POSIX-ish env var name: leading letter/underscore, then alnum/underscore.
# Stricter than the shell allows in practice but catches the typo class
# (`${env.}` empty, `${env.NAME WITH SPACES}`, `${env.NAME-WITH-DASH}`).
_ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Hop-by-hop / framing headers per RFC 7230 §6.1, plus framing headers and the
# common-but-non-standard Proxy-Connection. Overriding these breaks HTTP
# transport. Lower-case for case-insensitive comparison.
_RESERVED_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

_KNOWN_VARS = frozenset({"session_id", "request_path"})


def _validate_and_filter(parsed: dict[str, object]) -> dict[str, str]:
    """Validate parsed JSON object and drop reserved headers. Raises on bad input."""
    result: dict[str, str] = {}
    seen_lower: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(k, str):
            raise ValueError(f"UPSTREAM_HEADERS: header name must be a string, got {type(k).__name__}: {k!r}")
        if not isinstance(v, str):
            raise ValueError(f"UPSTREAM_HEADERS: value for {k!r} must be a string, got {type(v).__name__}")
        if not _HEADER_NAME_PATTERN.match(k):
            raise ValueError(f"UPSTREAM_HEADERS: header name {k!r} is not a valid RFC 7230 token")
        for match in _TEMPLATE_PATTERN.finditer(v):
            var = match.group(1)
            if var.startswith("env."):
                env_name = var[4:]
                if not _ENV_VAR_NAME_PATTERN.match(env_name):
                    raise ValueError(
                        f"UPSTREAM_HEADERS: invalid env var reference ${{env.{env_name}}} in {k!r} — "
                        "must be a non-empty identifier (letters, digits, underscore; no leading digit)"
                    )
        lower = k.lower()
        if lower in _RESERVED_HEADERS:
            logger.warning(
                "UPSTREAM_HEADERS: dropping hop-by-hop/framing header %r (overriding it would break HTTP transport)",
                k,
            )
            continue
        if lower in seen_lower:
            raise ValueError(f"UPSTREAM_HEADERS: duplicate header (case-insensitive): {seen_lower[lower]!r} and {k!r}")
        seen_lower[lower] = k
        result[k] = v
    return result


def _audit_template_vars(templates: dict[str, str]) -> None:
    """Log referenced env vars / template variables and flag the ones unset.

    The unset-at-startup warning catches the typo class where ``Helicone-Auth``
    expands to ``""`` (silently dropped) or ``"Bearer "`` (upstream rejects)
    because ``${env.HELICONE_API_KEY}`` is misspelled. Surfacing it at
    startup beats correlating with downstream 401s after the fact.
    """
    env_refs: set[str] = set()
    unknown: set[str] = set()
    for value in templates.values():
        for match in _TEMPLATE_PATTERN.finditer(value):
            var = match.group(1)
            if var.startswith("env."):
                env_refs.add(var[4:])
            elif var not in _KNOWN_VARS:
                unknown.add(var)
    if env_refs:
        logger.info(
            "UPSTREAM_HEADERS: referencing env vars: %s",
            ", ".join(sorted(env_refs)),
        )
        unset = {v for v in env_refs if not os.environ.get(v)}
        if unset:
            logger.warning(
                "UPSTREAM_HEADERS: referenced env var(s) are unset and will expand to empty: %s",
                ", ".join(sorted(unset)),
            )
    if unknown:
        logger.warning(
            "UPSTREAM_HEADERS: unknown template variable(s): %s — will be left unexpanded",
            ", ".join(sorted(f"${{{v}}}" for v in unknown)),
        )


@lru_cache(maxsize=1)
def _load_header_templates() -> dict[str, str]:
    """Parse and validate UPSTREAM_HEADERS once. Raises on misconfiguration.

    Returns empty dict if the env var is unset or empty (the only fail-open path).
    """
    raw = os.environ.get("UPSTREAM_HEADERS", "")
    if not raw:
        return {}
    parsed = json.loads(raw)  # raises JSONDecodeError on invalid JSON
    if not isinstance(parsed, dict):
        raise ValueError(f"UPSTREAM_HEADERS must be a JSON object, got {type(parsed).__name__}")
    result = _validate_and_filter(parsed)
    if result:
        logger.info("Loaded %d upstream header template(s)", len(result))
    _audit_template_vars(result)
    return result


def validate_upstream_headers_at_startup() -> None:
    """Force-load templates so misconfiguration fails the gateway at startup.

    Call from the app lifespan. Any ``json.JSONDecodeError`` or ``ValueError``
    raised here will propagate and prevent the gateway from coming up with a
    silently-disabled integration.
    """
    _load_header_templates()


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
        return match.group(0)  # Leave unknown variables unexpanded; warned at load time.

    result = _TEMPLATE_PATTERN.sub(_replace, template)
    # Strip CRLF + NUL: keep failures legible at the HTTP layer.
    return result.replace("\r", "").replace("\n", "").replace("\x00", "")


def expand_upstream_headers(
    session_id: str | None,
    request_path: str,
) -> dict[str, str] | None:
    """Expand upstream header templates for a single request.

    Returns ``None`` when no headers are configured (avoids unnecessary dict
    allocation on the hot path) or when every configured header expands to
    the empty string.
    """
    templates = _load_header_templates()
    if not templates:
        return None

    headers: dict[str, str] = {}
    for name, template in templates.items():
        value = _expand_template(template, session_id, request_path)
        if value:
            headers[name] = value
    return headers or None


def merge_forwarded_headers(
    base: dict[str, str] | None,
    upstream: dict[str, str] | None,
) -> dict[str, str] | None:
    """Merge ``upstream`` into ``base`` with ``base`` winning on collision.

    HTTP header names are case-insensitive, so the collision check is
    case-insensitive too — without it, ``Anthropic-Beta`` from the upstream
    config and ``anthropic-beta`` from the SDK would ship as two distinct
    dict entries and produce duplicate header lines on the wire.

    Returns ``None`` if both inputs are empty.
    """
    if not upstream:
        return base or None
    if base:
        reserved = {k.lower() for k in base}
        upstream = {k: v for k, v in upstream.items() if k.lower() not in reserved}
    merged: dict[str, str] = {**upstream, **base} if base else dict(upstream)
    return merged or None
