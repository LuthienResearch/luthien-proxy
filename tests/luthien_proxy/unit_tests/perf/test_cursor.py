from datetime import datetime, timezone

import pytest

from luthien_proxy.utils.cursor import decode_cursor, encode_cursor

_TS = datetime(2025, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
_SID = "perf-seed-100-0042"


def test_roundtrip():
    ts, sid = decode_cursor(encode_cursor(_TS, _SID, kind="sessions"), kind="sessions")
    assert ts == _TS
    assert sid == _SID


def test_roundtrip_with_microseconds():
    ts = datetime(2025, 5, 14, 12, 0, 0, 123456, tzinfo=timezone.utc)
    decoded_ts, decoded_sid = decode_cursor(encode_cursor(ts, _SID, kind="sessions"), kind="sessions")
    assert decoded_ts == ts
    assert decoded_sid == _SID


def test_tamper_rejected():
    token = encode_cursor(_TS, _SID, kind="sessions")
    bad = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ValueError, match="tampered|signature"):
        decode_cursor(bad, kind="sessions")


def test_short_token_rejected():
    with pytest.raises(ValueError):
        decode_cursor("abc", kind="sessions")


def test_idempotent():
    token1 = encode_cursor(_TS, _SID, kind="sessions")
    token2 = encode_cursor(_TS, _SID, kind="sessions")
    assert token1 == token2


def test_tiebreaker_distinguishes_same_timestamp():
    sid_a = "session-aaa"
    sid_b = "session-bbb"
    token_a = encode_cursor(_TS, sid_a, kind="sessions")
    token_b = encode_cursor(_TS, sid_b, kind="sessions")
    assert token_a != token_b
    _, key_a = decode_cursor(token_a, kind="sessions")
    _, key_b = decode_cursor(token_b, kind="sessions")
    assert key_a == sid_a
    assert key_b == sid_b


def test_different_key_types_roundtrip():
    event_id = "550e8400-e29b-41d4-a716-446655440000"
    ts, key = decode_cursor(encode_cursor(_TS, event_id, kind="turns"), kind="turns")
    assert ts == _TS
    assert key == event_id


def test_wrong_kind_rejected():
    sessions_token = encode_cursor(_TS, _SID, kind="sessions")
    with pytest.raises(ValueError, match="kind"):
        decode_cursor(sessions_token, kind="turns")

    turns_token = encode_cursor(_TS, "event-id-123", kind="turns")
    with pytest.raises(ValueError, match="kind"):
        decode_cursor(turns_token, kind="sessions")
