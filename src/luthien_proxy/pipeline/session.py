"""Session ID extraction from client requests.

Extracts session identifiers from incoming requests to enable tracking
conversations across multiple API calls.
"""

from __future__ import annotations

import re
from typing import Any

# Header name for clients to provide session ID (used by Claude Code and other integrations)
SESSION_ID_HEADER = "x-session-id"

# Pattern to extract session UUID from Anthropic metadata.user_id
# Format: user_<hash>_account__session_<uuid>
_SESSION_PATTERN = re.compile(r"_session_([a-f0-9-]+)$")


def extract_session_id_from_anthropic_body(body: dict[str, Any]) -> str | None:
    """Extract session ID from Anthropic API request body.

    Claude Code sends session info in the metadata.user_id field with format:
    user_<hash>_account__session_<uuid>

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

    match = _SESSION_PATTERN.search(user_id)
    if match:
        return match.group(1)

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
