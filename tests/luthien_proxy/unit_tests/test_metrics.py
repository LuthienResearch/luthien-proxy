# ABOUTME: Unit tests for Prometheus metrics instruments and MetricsAwareUsageCollector

import threading
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
            patch.object(telemetry_mod, "_metrics_lock", threading.Lock()),
            patch("luthien_proxy.telemetry.PrometheusMetricReader") as reader_cls,
            patch("luthien_proxy.telemetry.MeterProvider") as provider_cls,
            patch("luthien_proxy.telemetry.metrics"),
            patch("luthien_proxy.telemetry._build_resource"),
        ):
            telemetry_mod.configure_metrics()
            telemetry_mod.configure_metrics()
            assert reader_cls.call_count == 1
            assert provider_cls.call_count == 1

    def test_snapshot_and_reset_preserves_parent_counters(self):
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


class TestLatencyMiddleware:
    def _build_app(self, handler):
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.routing import Route

        from luthien_proxy.main import LatencyMiddleware

        return Starlette(
            routes=[Route("/v1/messages", handler, methods=["POST"])],
            middleware=[Middleware(LatencyMiddleware)],
        )

    def test_error_status_when_handler_raises(self):
        async def boom(request):
            raise RuntimeError("boom")

        app = self._build_app(boom)
        mock_duration = MagicMock()
        mock_active = MagicMock()

        with (
            patch("luthien_proxy.main.request_duration", mock_duration),
            patch("luthien_proxy.main.active_requests", mock_active),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/v1/messages")

        assert resp.status_code == 500
        mock_active.add.assert_any_call(1)
        mock_active.add.assert_any_call(-1)
        mock_duration.record.assert_called_once()
        attrs = mock_duration.record.call_args[0][1]
        assert attrs["status"] == "error"
        assert attrs["streaming"] == "false"

    def test_success_records_status_200_non_streaming(self):
        from starlette.responses import JSONResponse

        async def ok(request):
            return JSONResponse({"ok": True})

        app = self._build_app(ok)
        mock_duration = MagicMock()
        mock_active = MagicMock()

        with (
            patch("luthien_proxy.main.request_duration", mock_duration),
            patch("luthien_proxy.main.active_requests", mock_active),
        ):
            client = TestClient(app)
            resp = client.post("/v1/messages")

        assert resp.status_code == 200
        attrs = mock_duration.record.call_args[0][1]
        assert attrs["status"] == "200"
        assert attrs["streaming"] == "false"

    def test_streaming_response_labeled_as_streaming(self):
        from starlette.responses import StreamingResponse

        async def stream_gen():
            yield b"data: hello\n\n"

        async def sse(request):
            return StreamingResponse(stream_gen(), media_type="text/event-stream")

        app = self._build_app(sse)
        mock_duration = MagicMock()
        mock_active = MagicMock()

        with (
            patch("luthien_proxy.main.request_duration", mock_duration),
            patch("luthien_proxy.main.active_requests", mock_active),
        ):
            client = TestClient(app)
            resp = client.post("/v1/messages")

        assert resp.status_code == 200
        attrs = mock_duration.record.call_args[0][1]
        assert attrs["streaming"] == "true"

    def test_response_without_content_type_is_non_streaming(self):
        from starlette.responses import Response

        async def bare(request):
            return Response(content=b"ok", status_code=200)

        app = self._build_app(bare)
        mock_duration = MagicMock()
        mock_active = MagicMock()

        with (
            patch("luthien_proxy.main.request_duration", mock_duration),
            patch("luthien_proxy.main.active_requests", mock_active),
        ):
            client = TestClient(app)
            resp = client.post("/v1/messages")

        assert resp.status_code == 200
        attrs = mock_duration.record.call_args[0][1]
        assert attrs["streaming"] == "false"

    def test_non_messages_path_skips_metrics(self):
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        from luthien_proxy.main import LatencyMiddleware

        async def ok(request):
            return JSONResponse({"ok": True})

        app = Starlette(
            routes=[Route("/health", ok, methods=["GET"])],
            middleware=[Middleware(LatencyMiddleware)],
        )
        mock_duration = MagicMock()
        mock_active = MagicMock()

        with (
            patch("luthien_proxy.main.request_duration", mock_duration),
            patch("luthien_proxy.main.active_requests", mock_active),
        ):
            client = TestClient(app)
            resp = client.get("/health")

        assert resp.status_code == 200
        mock_duration.record.assert_not_called()
        mock_active.add.assert_not_called()
