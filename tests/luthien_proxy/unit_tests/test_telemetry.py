# ABOUTME: Unit tests for OpenTelemetry configuration and instrumentation
# ABOUTME: Tests tracing setup, logging configuration, and instrumentation helpers

"""Tests for telemetry module."""

import logging
from unittest.mock import Mock, patch

import pytest
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)

from luthien_proxy import telemetry
from luthien_proxy.settings import Settings
from luthien_proxy.telemetry import restore_context


# Six-tuple shape of telemetry._get_otel_config():
#   (otel_enabled, endpoint, service_name, service_version, environment, protocol)
def _config(
    *,
    enabled: bool = True,
    endpoint: str = "http://tempo:4318/v1/traces",
    protocol: str = "http/protobuf",
) -> tuple[bool, str, str, str, str, str]:
    return (enabled, endpoint, "svc", "1.0", "dev", protocol)


class TestSilenceOtelLoggers:
    """Test that noisy OTel/gRPC loggers are silenced."""

    def test_silences_known_noisy_loggers(self):
        """Loggers that spam errors on connection failure should be set to DEBUG."""
        telemetry._silence_otel_loggers()

        for name in (
            "opentelemetry.exporter.otlp.proto.grpc.exporter",
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
            "opentelemetry.sdk.trace.export",
            "grpc._channel",
            "grpc._plugin_wrapping",
        ):
            assert logging.getLogger(name).level == logging.DEBUG


class TestConfigureTracing:
    """Test tracing configuration."""

    def test_returns_valid_tracer(self):
        """Test that configure_tracing always returns a valid tracer."""
        result = telemetry.configure_tracing()
        assert result is not None
        assert isinstance(result, trace.Tracer)

    @patch("luthien_proxy.telemetry._get_otel_config")
    @patch("luthien_proxy.telemetry._silence_otel_loggers")
    def test_disabled_silences_loggers(self, mock_silence, mock_config):
        """When OTel is disabled, noisy loggers are silenced."""
        mock_config.return_value = _config(enabled=False, endpoint="")
        telemetry.configure_tracing()
        mock_silence.assert_called_once()

    @patch("luthien_proxy.telemetry._get_otel_config")
    @patch("luthien_proxy.telemetry._silence_otel_loggers")
    def test_enabled_does_not_silence_loggers(self, mock_silence, mock_config):
        """When OTel is enabled, loggers are NOT silenced — connection errors should be visible."""
        mock_config.return_value = _config()
        telemetry.configure_tracing()
        mock_silence.assert_not_called()

    @patch("luthien_proxy.telemetry._get_otel_config")
    def test_disabled_logs_at_debug_not_info(self, mock_config):
        """When OTel is disabled, the message should be DEBUG, not INFO."""
        mock_config.return_value = _config(enabled=False, endpoint="")
        with patch.object(telemetry.logger, "debug") as mock_debug, patch.object(telemetry.logger, "info") as mock_info:
            telemetry.configure_tracing()
            mock_debug.assert_called_once()
            mock_info.assert_not_called()


class TestBuildOtlpExporter:
    """Test exporter dispatch on the protocol setting."""

    def test_default_protocol_returns_http_exporter(self):
        """Default protocol (http/protobuf) builds the HTTP exporter."""
        exporter = telemetry._build_otlp_exporter("http/protobuf", "http://tempo:4318/v1/traces")
        assert isinstance(exporter, HttpSpanExporter)

    def test_grpc_protocol_returns_grpc_exporter(self):
        """Explicit grpc protocol builds the gRPC exporter."""
        exporter = telemetry._build_otlp_exporter("grpc", "http://tempo:4317")
        assert isinstance(exporter, GrpcSpanExporter)

    @pytest.mark.parametrize("bad_value", ["", "http", "HTTP/PROTOBUF", "tcp", "rest"])
    def test_unknown_protocol_raises_value_error(self, bad_value):
        """Unknown protocol values fail loud at startup — never silently fall back."""
        with pytest.raises(ValueError, match="OTEL_EXPORTER_OTLP_PROTOCOL"):
            telemetry._build_otlp_exporter(bad_value, "http://tempo:4318/v1/traces")

    @patch("luthien_proxy.telemetry._get_otel_config")
    def test_configure_tracing_propagates_unknown_protocol_error(self, mock_config):
        """An invalid protocol surfaces as ValueError from configure_tracing."""
        mock_config.return_value = _config(protocol="not-a-real-protocol")
        with pytest.raises(ValueError):
            telemetry.configure_tracing()


class TestProtocolEnvVarBinding:
    """Guard that the OTEL_EXPORTER_OTLP_PROTOCOL env var is actually wired up.

    Regression guard: an earlier version of this PR named the field
    ``otel_exporter_protocol``, which auto-derives to
    ``OTEL_EXPORTER_PROTOCOL`` — silently breaking the documented
    ``OTEL_EXPORTER_OTLP_PROTOCOL`` escape hatch.
    """

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
        settings = Settings(_env_file=None)
        assert settings.otel_exporter_otlp_protocol == "grpc"

    def test_default_is_http_protobuf(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.otel_exporter_otlp_protocol == "http/protobuf"

    def test_default_endpoint_targets_http_port_with_v1_traces_path(self, monkeypatch):
        """Default endpoint must match the default HTTP/protobuf protocol."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        settings = Settings(_env_file=None)
        assert settings.otel_exporter_otlp_endpoint == "http://tempo:4318/v1/traces"


class TestInstrumentApp:
    """Test FastAPI instrumentation."""

    def test_does_not_raise_exception(self):
        """Test that instrument_app doesn't raise exceptions."""
        mock_app = Mock()
        telemetry.instrument_app(mock_app)
        # No exception should be raised


class TestInstrumentRedis:
    """Test Redis instrumentation."""

    def test_does_not_raise_exception(self):
        """Test that instrument_redis doesn't raise exceptions."""
        telemetry.instrument_redis()
        # No exception should be raised


class TestConfigureLogging:
    """Test logging configuration."""

    def test_configures_root_logger(self):
        """Test that logging configuration sets up root logger with trace correlation."""
        telemetry.configure_logging()

        root_logger = logging.getLogger()
        assert len(root_logger.handlers) >= 1
        assert root_logger.level == logging.INFO

    def test_formatter_adds_trace_context(self):
        """Test that formatter adds trace_id and span_id to records."""
        telemetry.configure_logging()

        root_logger = logging.getLogger()
        handler = root_logger.handlers[0]

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )

        formatted = handler.formatter.format(record)

        # Should contain trace_id and span_id fields
        assert "trace_id" in formatted
        assert "span_id" in formatted


class TestSetupTelemetry:
    """Test setup_telemetry orchestration."""

    @patch("luthien_proxy.telemetry.configure_tracing")
    @patch("luthien_proxy.telemetry.configure_logging")
    @patch("luthien_proxy.telemetry.instrument_redis")
    @patch("luthien_proxy.telemetry.instrument_app")
    def test_without_app(
        self, mock_instrument_app, mock_instrument_redis, mock_configure_logging, mock_configure_tracing
    ):
        """Test setup without app calls all setup functions except instrument_app."""
        mock_tracer = Mock()
        mock_configure_tracing.return_value = mock_tracer

        result = telemetry.setup_telemetry()

        mock_configure_tracing.assert_called_once()
        mock_configure_logging.assert_called_once()
        mock_instrument_redis.assert_called_once()
        mock_instrument_app.assert_not_called()
        assert result == mock_tracer

    @patch("luthien_proxy.telemetry.configure_tracing")
    @patch("luthien_proxy.telemetry.configure_logging")
    @patch("luthien_proxy.telemetry.instrument_redis")
    @patch("luthien_proxy.telemetry.instrument_app")
    def test_with_app(self, mock_instrument_app, mock_instrument_redis, mock_configure_logging, mock_configure_tracing):
        """Test setup with app calls all setup functions including instrument_app."""
        mock_tracer = Mock()
        mock_configure_tracing.return_value = mock_tracer
        mock_app = Mock()

        result = telemetry.setup_telemetry(mock_app)

        mock_configure_tracing.assert_called_once()
        mock_configure_logging.assert_called_once()
        mock_instrument_redis.assert_called_once()
        mock_instrument_app.assert_called_once_with(mock_app)
        assert result == mock_tracer


class TestRestoreContext:
    """Tests for the restore_context context manager."""

    @patch("luthien_proxy.telemetry.detach")
    @patch("luthien_proxy.telemetry.attach")
    def test_attaches_context_on_entry(self, mock_attach, mock_detach):
        """Test that the context is attached when entering the context manager."""
        mock_token = Mock()
        mock_attach.return_value = mock_token
        ctx = Context()

        with restore_context(ctx):
            mock_attach.assert_called_once_with(ctx)

    @patch("luthien_proxy.telemetry.detach")
    @patch("luthien_proxy.telemetry.attach")
    def test_yields_token(self, mock_attach, mock_detach):
        """Test that the context manager yields the attach token."""
        mock_token = Mock()
        mock_attach.return_value = mock_token
        ctx = Context()

        with restore_context(ctx) as token:
            assert token is mock_token

    @patch("luthien_proxy.telemetry.detach")
    @patch("luthien_proxy.telemetry.attach")
    def test_detaches_on_normal_exit(self, mock_attach, mock_detach):
        """Test that detach is called on normal exit."""
        mock_token = Mock()
        mock_attach.return_value = mock_token
        ctx = Context()

        with restore_context(ctx):
            pass

        mock_detach.assert_called_once_with(mock_token)

    @patch("luthien_proxy.telemetry.detach")
    @patch("luthien_proxy.telemetry.attach")
    def test_detaches_on_exception(self, mock_attach, mock_detach):
        """Test that detach is called even when an exception occurs."""
        mock_token = Mock()
        mock_attach.return_value = mock_token
        ctx = Context()

        with pytest.raises(ValueError, match="test error"):
            with restore_context(ctx):
                raise ValueError("test error")

        mock_detach.assert_called_once_with(mock_token)

    @patch("luthien_proxy.telemetry.detach")
    @patch("luthien_proxy.telemetry.attach")
    def test_exception_propagates(self, mock_attach, mock_detach):
        """Test that exceptions propagate through the context manager."""
        mock_attach.return_value = Mock()
        ctx = Context()

        with pytest.raises(RuntimeError, match="should propagate"):
            with restore_context(ctx):
                raise RuntimeError("should propagate")

    @patch("luthien_proxy.telemetry.detach")
    @patch("luthien_proxy.telemetry.attach")
    def test_attach_then_detach_ordering(self, mock_attach, mock_detach):
        """Test that attach happens before detach."""
        call_order = []
        mock_token = Mock()

        def track_attach(ctx):
            call_order.append("attach")
            return mock_token

        def track_detach(token):
            call_order.append("detach")

        mock_attach.side_effect = track_attach
        mock_detach.side_effect = track_detach
        ctx = Context()

        with restore_context(ctx):
            assert call_order == ["attach"]

        assert call_order == ["attach", "detach"]
