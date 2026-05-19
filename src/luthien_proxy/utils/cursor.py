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

CursorKind = Literal["sessions", "turns"]


def _get_hmac_key() -> bytes:
    return get_settings().cursor_hmac_key.encode()


def encode_cursor(last_ts: datetime, last_key: str, kind: CursorKind) -> str:
    """Encode a composite pagination cursor scoped to the given endpoint kind."""
    payload = json.dumps(
        {"ts": last_ts.isoformat(), "sid": last_key, "kind": kind},
        separators=(",", ":"),
    ).encode()

    # 8 bytes (64 bits) is sufficient for pagination integrity: the threat model
    # is accidental corruption and casual tampering, not a dedicated adversary
    # with offline brute-force capability. Cursors are admin-auth-gated and
    # encode only a pagination position, not access-control decisions.
    sig = hmac.new(_get_hmac_key(), payload, hashlib.sha256).digest()[:8]
    token = base64.urlsafe_b64encode(payload + sig).rstrip(b"=").decode()
    return token


def decode_cursor(token: str, kind: CursorKind) -> tuple[datetime, str]:
    """Decode and verify a cursor token.

    Raises:
        ValueError: If token is malformed, tampered, wrong kind, or invalid.
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
        token_kind = data["kind"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid cursor: payload parse failed: {exc}") from exc

    if token_kind != kind:
        raise ValueError(f"Invalid cursor: expected kind={kind!r}, got {token_kind!r}")

    return ts, sid
