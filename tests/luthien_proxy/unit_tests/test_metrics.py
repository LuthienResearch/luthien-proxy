# ABOUTME: Unit tests for Prometheus metrics instruments and MetricsAwareUsageCollector

from unittest.mock import MagicMock, patch

from luthien_proxy.metrics import MetricsAwareUsageCollector


class TestMetricsAwareUsageCollector:
    def test_record_completed_increments_request_counter(self):
        collector = MetricsAwareUsageCollector()
        mock_counter = MagicMock()
        with patch("luthien_proxy.metrics.request_counter", mock_counter):
            collector.record_completed(is_streaming=False)
        mock_counter.add.assert_called_once_with(1, {"streaming": "false"})

    def test_record_completed_streaming_label(self):
        collector = MetricsAwareUsageCollector()
        mock_counter = MagicMock()
        with patch("luthien_proxy.metrics.request_counter", mock_counter):
            collector.record_completed(is_streaming=True)
        mock_counter.add.assert_called_once_with(1, {"streaming": "true"})

    def test_record_completed_also_updates_parent(self):
        collector = MetricsAwareUsageCollector()
        with patch("luthien_proxy.metrics.request_counter"):
            collector.record_completed(is_streaming=False)
        snapshot = collector.snapshot_and_reset()
        assert snapshot["requests_completed"] == 1
        assert snapshot["non_streaming_requests"] == 1

    def test_record_tokens_increments_token_counter(self):
        collector = MetricsAwareUsageCollector()
        mock_counter = MagicMock()
        with patch("luthien_proxy.metrics.token_counter", mock_counter):
            collector.record_tokens(input_tokens=100, output_tokens=50)
        assert mock_counter.add.call_count == 2
        mock_counter.add.assert_any_call(100, {"type": "input"})
        mock_counter.add.assert_any_call(50, {"type": "output"})

    def test_record_tokens_also_updates_parent(self):
        collector = MetricsAwareUsageCollector()
        with patch("luthien_proxy.metrics.token_counter"):
            collector.record_tokens(input_tokens=10, output_tokens=20)
        snapshot = collector.snapshot_and_reset()
        assert snapshot["input_tokens"] == 10
        assert snapshot["output_tokens"] == 20

    def test_record_tokens_zero_values_still_emitted(self):
        collector = MetricsAwareUsageCollector()
        mock_counter = MagicMock()
        with patch("luthien_proxy.metrics.token_counter", mock_counter):
            collector.record_tokens(input_tokens=0, output_tokens=0)
        assert mock_counter.add.call_count == 2
        mock_counter.add.assert_any_call(0, {"type": "input"})
        mock_counter.add.assert_any_call(0, {"type": "output"})

    def test_snapshot_and_reset_unaffected_by_metrics(self):
        collector = MetricsAwareUsageCollector()
        with patch("luthien_proxy.metrics.request_counter"), patch("luthien_proxy.metrics.token_counter"):
            collector.record_completed(is_streaming=True)
            collector.record_tokens(input_tokens=5, output_tokens=3)
        snapshot = collector.snapshot_and_reset()
        assert snapshot["requests_completed"] == 1
        assert snapshot["streaming_requests"] == 1
        assert snapshot["input_tokens"] == 5
        assert snapshot["output_tokens"] == 3
        snapshot2 = collector.snapshot_and_reset()
        assert snapshot2["requests_completed"] == 0
