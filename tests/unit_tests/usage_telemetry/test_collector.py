"""Tests for usage telemetry collector."""

from luthien_proxy.usage_telemetry.collector import UsageCollector


class TestUsageCollector:
    def test_record_completed_streaming(self):
        c = UsageCollector()
        c.record_completed(is_streaming=True)
        snapshot = c.snapshot_and_reset()
        assert snapshot["requests_completed"] == 1
        assert snapshot["streaming_requests"] == 1
        assert snapshot["non_streaming_requests"] == 0

    def test_record_completed_non_streaming(self):
        c = UsageCollector()
        c.record_completed(is_streaming=False)
        snapshot = c.snapshot_and_reset()
        assert snapshot["requests_completed"] == 1
        assert snapshot["streaming_requests"] == 0
        assert snapshot["non_streaming_requests"] == 1

    def test_record_session_deduplicates(self):
        c = UsageCollector()
        c.record_session("session-1")
        c.record_session("session-1")
        c.record_session("session-2")
        snapshot = c.snapshot_and_reset()
        assert snapshot["sessions_with_ids"] == 2

    def test_record_session_ignores_none(self):
        c = UsageCollector()
        c.record_session(None)
        snapshot = c.snapshot_and_reset()
        assert snapshot["sessions_with_ids"] == 0

    def test_snapshot_resets_counters(self):
        c = UsageCollector()
        c.record_accepted()
        c.record_tokens(input_tokens=100, output_tokens=50)
        c.record_session("s1")
        first = c.snapshot_and_reset()
        assert first["requests_accepted"] == 1

        second = c.snapshot_and_reset()
        assert second["requests_accepted"] == 0
        assert second["input_tokens"] == 0
        assert second["sessions_with_ids"] == 0
