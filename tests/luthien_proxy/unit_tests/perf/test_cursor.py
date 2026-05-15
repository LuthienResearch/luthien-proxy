from datetime import datetime, timezone

import pytest

from luthien_proxy.perf.cursor import cursor_where_clause, decode_cursor, encode_cursor

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
