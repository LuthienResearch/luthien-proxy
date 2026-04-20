"""Session ID and user identity extraction from client requests.

Extracts session identifiers and user identities from incoming requests to enable
tracking conversations across multiple API calls and attributing requests to users.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

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
    # Strip control characters (0x00-0x1F, 0x7F) that could corrupt logs or
    # downstream JSON. Keep printable ASCII + unicode identity letters.
    cleaned = "".join(ch for ch in value if ord(ch) >= 0x20 and ord(ch) != 0x7F).strip()
    if not cleaned:
        return None
    return cleaned[:_USER_ID_MAX_LENGTH]


def extract_user_id_from_bearer_token(token: str | None) -> str | None:
    """Extract user identity from the ``sub`` claim of a Bearer JWT token.

    Decodes the JWT payload without signature verification — this is used for
    identity attribution only, not authentication. Malformed or opaque tokens
    (e.g. Anthropic API keys) return None gracefully.

    Args:
        token: Raw Bearer token string (without "Bearer " prefix), or None

    Returns:
        The ``sub`` claim string if present and valid, None otherwise
    """
    if not token:
        return None

    # JWTs have exactly three dot-separated parts
    parts = token.split(".")
    if len(parts) != 3:
        return None

    payload_b64 = parts[1]
    try:
        # Add padding back — JWT base64url strips trailing '='
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    sub = payload.get("sub")
    return sub if isinstance(sub, str) and sub else None


__all__ = [
    "SESSION_ID_HEADER",
    "USER_ID_HEADER",
    "extract_session_id_from_anthropic_body",
    "extract_session_id_from_headers",
    "extract_user_id_from_bearer_token",
    "extract_user_id_from_headers",
]
