# ABOUTME: Unit tests for OpenTelemetry configuration and instrumentation
# ABOUTME: Tests tracing setup, logging configuration, and instrumentation helpers

"""Tests for telemetry module."""

import logging
from unittest.mock import Mock, patch

from opentelemetry import trace

from luthien_proxy import telemetry


class TestConfigureTracing:
    """Test tracing configuration."""

    def test_returns_valid_tracer(self):
        """Test that configure_tracing always returns a valid tracer."""
        result = telemetry.configure_tracing()
        assert result is not None
        assert isinstance(result, trace.Tracer)


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
