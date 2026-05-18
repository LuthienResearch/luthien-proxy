from datetime import datetime, timezone

import pytest

from luthien_proxy.utils.cursor import cursor_where_clause, decode_cursor, encode_cursor

_TS = datetime(2025, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
_SID = "perf-seed-100-0042"


def test_roundtrip():
    ts, sid = decode_cursor(encode_cursor(_TS, _SID))
    assert ts == _TS
    assert sid == _SID


def test_roundtrip_with_microseconds():
    ts = datetime(2025, 5, 14, 12, 0, 0, 123456, tzinfo=timezone.utc)
    decoded_ts, decoded_sid = decode_cursor(encode_cursor(ts, _SID))
    assert decoded_ts == ts
    assert decoded_sid == _SID


def test_tamper_rejected():
    token = encode_cursor(_TS, _SID)
    bad = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ValueError, match="tampered|signature"):
        decode_cursor(bad)


def test_short_token_rejected():
    with pytest.raises(ValueError):
        decode_cursor("abc")


def test_composite_where_clause_sqlite():
    clause = cursor_where_clause("sqlite")
    assert clause == "(last_ts, session_id) < (:cursor_ts, :cursor_sid)"


def test_composite_where_clause_custom_cols():
    clause = cursor_where_clause("postgres", ts_col="created_at", sid_col="sid")
    assert clause == "(created_at, sid) < (:cursor_ts, :cursor_sid)"


def test_idempotent():
    token1 = encode_cursor(_TS, _SID)
    token2 = encode_cursor(_TS, _SID)
    assert token1 == token2


def test_tiebreaker_distinguishes_same_timestamp():
    sid_a = "session-aaa"
    sid_b = "session-bbb"
    token_a = encode_cursor(_TS, sid_a)
    token_b = encode_cursor(_TS, sid_b)
    assert token_a != token_b
    _, key_a = decode_cursor(token_a)
    _, key_b = decode_cursor(token_b)
    assert key_a == sid_a
    assert key_b == sid_b


def test_different_key_types_roundtrip():
    event_id = "550e8400-e29b-41d4-a716-446655440000"
    ts, key = decode_cursor(encode_cursor(_TS, event_id))
    assert ts == _TS
    assert key == event_id
