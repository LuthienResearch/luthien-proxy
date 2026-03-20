"""Session ID extraction from client requests.

Extracts session identifiers from incoming requests to enable tracking
conversations across multiple API calls.
"""

from __future__ import annotations

import json
import re
from typing import Any

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


__all__ = [
    "SESSION_ID_HEADER",
    "extract_session_id_from_anthropic_body",
    "extract_session_id_from_headers",
]
