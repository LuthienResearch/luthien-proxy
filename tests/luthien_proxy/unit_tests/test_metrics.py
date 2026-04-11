# ABOUTME: Unit tests for Prometheus metrics instruments and MetricsAwareUsageCollector

import time
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import luthien_proxy.telemetry as telemetry_mod
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

    def test_record_tokens_skips_zero_values(self):
        collector = MetricsAwareUsageCollector()
        mock_counter = MagicMock()
        with patch("luthien_proxy.metrics.token_counter", mock_counter):
            collector.record_tokens(input_tokens=0, output_tokens=0)
        mock_counter.add.assert_not_called()

    def test_configure_metrics_idempotent(self):
        with (
            patch.object(telemetry_mod, "_metrics_configured", False),
            patch.object(telemetry_mod, "_metrics_lock", telemetry_mod.threading.Lock()),
            patch("luthien_proxy.telemetry.PrometheusMetricReader") as reader_cls,
            patch("luthien_proxy.telemetry.MeterProvider") as provider_cls,
            patch("luthien_proxy.telemetry.metrics"),
            patch("luthien_proxy.telemetry._build_resource"),
        ):
            telemetry_mod.configure_metrics()
            telemetry_mod.configure_metrics()
            assert reader_cls.call_count == 1
            assert provider_cls.call_count == 1

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


class TestLatencyMiddlewareErrorPath:
    def test_error_status_when_call_next_raises(self):
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.routing import Route

        mock_duration = MagicMock()
        mock_active = MagicMock()

        class _TestMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if request.url.path != "/v1/messages":
                    return await call_next(request)
                mock_active.add(1)
                t0 = time.perf_counter()
                response = None
                try:
                    response = await call_next(request)
                    return response
                finally:
                    status = str(response.status_code) if response is not None else "error"
                    mock_duration.record(time.perf_counter() - t0, {"status": status})
                    mock_active.add(-1)

        async def boom(request):
            raise RuntimeError("boom")

        app = Starlette(
            routes=[Route("/v1/messages", boom, methods=["POST"])],
            middleware=[Middleware(_TestMiddleware)],
        )

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/v1/messages")
        assert resp.status_code == 500

        mock_active.add.assert_any_call(1)
        mock_active.add.assert_any_call(-1)
        mock_duration.record.assert_called_once()
        recorded_attrs = mock_duration.record.call_args[0][1]
        assert recorded_attrs["status"] == "error"
