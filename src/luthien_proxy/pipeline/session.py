"""Session ID extraction from client requests.

Extracts session identifiers from incoming requests to enable tracking
conversations across multiple API calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from luthien_proxy.credentials import Credential

# Header name for clients to provide session ID (used by Claude Code and other integrations)
SESSION_ID_HEADER = "x-session-id"

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


# Pattern to extract user hash from API key mode metadata.user_id
# Format: user_<hash>_account__session_<uuid>
# Restrict capture group to alphanumeric + underscore + dash to prevent
# adversarial metadata.user_id values from injecting XSS payloads into
# the stored user_hash, which is rendered in the admin history UI.
_USER_HASH_PATTERN = re.compile(r"^user_([A-Za-z0-9_-]+?)_account__")

# Number of hex characters to keep from SHA-256 digest (64 bits of entropy)
_CREDENTIAL_HASH_LENGTH = 16


def extract_user_hash(body: dict[str, Any], credential: Credential | None) -> str | None:
    """Extract a stable user identifier from request metadata or credential.

    Claude Code sends metadata.user_id in two formats:
    - API key mode: ``user_<hash>_account__session_<uuid>`` -> extract ``<hash>``
    - OAuth mode: JSON ``{"device_id": "...", "session_id": "..."}`` -> hash credential

    Falls back to hashing the credential value if metadata doesn't contain a user ID.

    **Trust model**: The user_hash is *client-asserted* in API-key mode — any
    client holding a valid credential can forge a metadata.user_id that maps to
    another user's hash. This is acceptable because user_hash is used only for
    observability grouping in the admin history UI, not for access control.
    The admin UI is behind separate authentication. If cryptographic binding is
    needed in the future, HMAC(credential, metadata_user_id) would prevent
    spoofing without changing the external interface.

    Args:
        body: Raw request body as dict (expected to have optional ``metadata.user_id`` field)
        credential: The authenticated credential for this request, used as fallback identifier

    Returns:
        Stable user hash string, or None if no identifying info is available.
    """
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        user_id = metadata.get("user_id")
        if isinstance(user_id, str):
            # Try API key format: user_<hash>_account__session_<uuid>
            match = _USER_HASH_PATTERN.match(user_id)
            if match:
                return match.group(1)

    # Fallback: hash the credential value
    if credential is not None:
        return hashlib.sha256(credential.value.encode()).hexdigest()[:_CREDENTIAL_HASH_LENGTH]

    return None


__all__ = [
    "SESSION_ID_HEADER",
    "extract_session_id_from_anthropic_body",
    "extract_session_id_from_headers",
    "extract_user_hash",
]
