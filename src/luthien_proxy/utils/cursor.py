"""Opaque cursor helpers for composite (last_ts, session_id) pagination.

Cursors are base64url-encoded, HMAC-signed tokens that encode a composite
pagination key. Clients cannot forge or tamper with cursors.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Literal

from luthien_proxy.settings import get_settings


def _get_hmac_key() -> bytes:
    key = get_settings().cursor_hmac_key
    return key.encode() if isinstance(key, str) else key


def encode_cursor(last_ts: datetime, last_key: str) -> str:
    """Encode a composite pagination cursor.

    Args:
        last_ts: Timestamp of the last item on the current page.
        last_key: Opaque tiebreaker for rows sharing the same timestamp
            (typically session_id or event_id depending on the query).

    Returns:
        Opaque base64url-encoded cursor string.
    """
    payload = json.dumps(
        {"ts": last_ts.isoformat(), "sid": last_key},
        separators=(",", ":"),
    ).encode()

    # 8 bytes (64 bits) is sufficient for pagination integrity: the threat model
    # is accidental corruption and casual tampering, not a dedicated adversary
    # with offline brute-force capability. Cursors are admin-auth-gated and
    # encode only a pagination position, not access-control decisions.
    sig = hmac.new(_get_hmac_key(), payload, hashlib.sha256).digest()[:8]
    token = base64.urlsafe_b64encode(payload + sig).rstrip(b"=").decode()
    return token


def decode_cursor(token: str) -> tuple[datetime, str]:
    """Decode and verify a cursor token.

    Args:
        token: Opaque cursor string from encode_cursor.

    Returns:
        Tuple of (last_ts, last_session_id).

    Raises:
        ValueError: If token is malformed, tampered, or invalid.
    """
    try:
        padded = token + "=" * (4 - len(token) % 4)
        raw = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ValueError(f"Invalid cursor: base64 decode failed: {exc}") from exc

    if len(raw) < 9:
        raise ValueError("Invalid cursor: too short")

    payload = raw[:-8]
    sig = raw[-8:]

    expected_sig = hmac.new(_get_hmac_key(), payload, hashlib.sha256).digest()[:8]
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("Invalid cursor: signature mismatch (tampered)")

    try:
        data = json.loads(payload)
        ts = datetime.fromisoformat(data["ts"])
        sid = data["sid"]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ValueError(f"Invalid cursor: payload parse failed: {exc}") from exc

    return ts, sid


def cursor_where_clause(
    backend: Literal["sqlite", "postgres"],
    ts_col: str = "last_ts",
    sid_col: str = "session_id",
) -> str:
    """Return a SQL WHERE fragment for composite cursor pagination.

    Uses (ts, sid) < (cursor_ts, cursor_sid) semantics to handle tied timestamps.

    Args:
        backend: Database backend ("sqlite" or "postgres").
        ts_col: Column name for the timestamp.
        sid_col: Column name for the session ID.

    Returns:
        SQL fragment string (without WHERE keyword). Uses :cursor_ts and :cursor_sid
        as named parameters.
    """
    return f"({ts_col}, {sid_col}) < (:cursor_ts, :cursor_sid)"
