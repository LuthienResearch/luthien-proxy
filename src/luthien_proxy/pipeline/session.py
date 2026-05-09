"""Session ID and user identity extraction from client requests.

Extracts session identifiers and user identities from incoming requests to enable
tracking conversations across multiple API calls and attributing requests to users.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Header name for clients to provide session ID (used by Claude Code and other integrations)
SESSION_ID_HEADER = "x-session-id"

# Header name for clients or upstream proxies to provide user identity.
# Takes precedence over JWT sub claim extraction.
USER_ID_HEADER = "x-luthien-user-id"

# Pattern to extract session UUID from Anthropic metadata.user_id
# Format: user_<hash>_account__session_<uuid>
_SESSION_PATTERN = re.compile(r"_session_([a-f0-9-]+)$")


def extract_session_id_from_anthropic_body(body: dict[str, Any]) -> str | None:
    """Extract session ID from Anthropic API request body.

    Claude Code sends session info in the metadata.user_id field in two formats:

    1. API key mode: ``user_<hash>_account__session_<uuid>``
    2. OAuth mode: JSON string ``{"device_id": "...", "session_id": "..."}``

    Args:
        body: Raw request body as dict

    Returns:
        Session UUID if found, None otherwise
    """
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        return None

    user_id = metadata.get("user_id")
    if not isinstance(user_id, str):
        return None

    if len(user_id) > 8192:
        logger.debug("metadata.user_id exceeds 8 KB (%d bytes) — skipping", len(user_id))
        return None

    # Try API key format: user_<hash>_account__session_<uuid>
    match = _SESSION_PATTERN.search(user_id)
    if match:
        return match.group(1)

    # Try OAuth format: JSON string with session_id field
    try:
        parsed = json.loads(user_id)
        if isinstance(parsed, dict):
            session_id = parsed.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
    except (json.JSONDecodeError, TypeError):
        pass

    return None


def extract_session_id_from_headers(headers: dict[str, str]) -> str | None:
    """Extract session ID from request headers.

    Clients can provide session ID via x-session-id header (used by Claude Code
    and other integrations).

    Args:
        headers: Request headers (keys should be lowercase)

    Returns:
        Session ID if header present and non-empty, None otherwise
    """
    value = headers.get(SESSION_ID_HEADER)
    # Normalize empty strings to None for consistent handling
    return value if value else None


_USER_ID_MAX_LENGTH = 256


def _sanitize_user_id(value: str) -> str | None:
    """Sanitize user ID by stripping control characters and truncating.

    Args:
        value: Raw user ID string

    Returns:
        Sanitized user ID (max 256 chars, no control chars), or None if empty
    """
    cleaned = "".join(ch for ch in value if ord(ch) >= 0x20 and ord(ch) != 0x7F).strip()
    return cleaned[:_USER_ID_MAX_LENGTH] if cleaned else None


def extract_user_id_from_headers(headers: dict[str, str], *, trust_header: bool) -> str | None:
    """Extract user identity from the X-Luthien-User-Id request header.

    Only consulted when ``trust_header`` is True (set via TRUST_USER_ID_HEADER config).
    Values are trimmed, truncated to 256 chars, and stripped of control characters
    to prevent log injection and storage overflow.

    Args:
        headers: Request headers (keys should be lowercase)
        trust_header: When False, always returns None regardless of header value.
            Controlled by TRUST_USER_ID_HEADER setting.

    Returns:
        Sanitized user ID string, or None if header absent, empty, or untrusted
    """
    if not trust_header:
        return None
    value = headers.get(USER_ID_HEADER)
    if not value:
        return None
    return _sanitize_user_id(value)


def extract_user_id_from_authorization_header(header_value: str | None) -> str | None:
    """Extract user identity from a raw ``Authorization`` header value.

    Convenience wrapper that strips the ``Bearer `` scheme and delegates to
    :func:`extract_user_id_from_bearer_token`. Centralizes the prefix logic so
    callers don't reimplement it (case-insensitive scheme match, empty token
    rejection).

    Args:
        header_value: Raw value of the ``Authorization`` header, or None.

    Returns:
        The JWT ``sub`` claim string if the header carries a decodable Bearer
        JWT, None otherwise (including for non-Bearer schemes like Basic).
    """
    if not header_value:
        return None
    if not header_value.lower().startswith("bearer "):
        return None
    stripped = header_value[7:].strip()
    return extract_user_id_from_bearer_token(stripped) if stripped else None


def extract_user_id_from_bearer_token(token: str | None) -> str | None:
    """Extract user identity from the ``sub`` claim of a Bearer JWT token.

    USER-ASSERTED, NOT AUTHENTICATED. Decodes the JWT payload without signature
    verification — this is used for identity attribution only, not authentication.
    Never use this value for access control or security decisions.

    The ``exp`` claim is intentionally ignored: rejecting expired JWTs creates
    attribution gaps without adding any security guarantee (the signature is
    not checked, so a forged-expiry token would pass anyway).

    NOTE: this path only fires for OAuth-passthrough clients that send
    ``Authorization: Bearer <jwt>``. Anthropic SDK clients using ``x-api-key``
    do not carry a Bearer token — for those deployments, only the
    ``X-Luthien-User-Id`` header path produces a user_id.

    Malformed or opaque tokens (e.g. Anthropic API keys) return None gracefully.

    Args:
        token: Raw Bearer token string (without "Bearer " prefix), or None

    Returns:
        The ``sub`` claim string if present and valid, None otherwise
    """
    if not token:
        return None

    if len(token) > 8192:
        return None

    # JWTs have exactly three dot-separated parts
    parts = token.split(".")
    if len(parts) != 3:
        return None

    payload_b64 = parts[1]
    try:
        # Add padding back — JWT base64url strips trailing '='
        payload_b64 += "=" * ((-len(payload_b64)) % 4)
        # Translate base64url alphabet to standard so we can use validate=True.
        # validate=True surfaces malformed tokens early instead of silently
        # discarding non-base64 chars and feeding garbage to json.loads.
        standard_b64 = payload_b64.encode("ascii").translate(bytes.maketrans(b"-_", b"+/"))
        payload_bytes = base64.b64decode(standard_b64, validate=True)
        payload = json.loads(payload_bytes)
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    return _sanitize_user_id(sub)


__all__ = [
    "SESSION_ID_HEADER",
    "USER_ID_HEADER",
    "extract_session_id_from_anthropic_body",
    "extract_session_id_from_headers",
    "extract_user_id_from_authorization_header",
    "extract_user_id_from_bearer_token",
    "extract_user_id_from_headers",
]
