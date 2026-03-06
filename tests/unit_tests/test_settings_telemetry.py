"""Tests for telemetry-related settings."""

from luthien_proxy.settings import Settings


class TestTelemetrySettings:
    def test_usage_telemetry_defaults_to_none(self):
        """Env var not set means None (defer to DB)."""
        s = Settings(proxy_api_key="k", admin_api_key="k", database_url="postgres://x")
        assert s.usage_telemetry is None

    def test_usage_telemetry_true(self):
        s = Settings(
            proxy_api_key="k",
            admin_api_key="k",
            database_url="postgres://x",
            usage_telemetry=True,
        )
        assert s.usage_telemetry is True

    def test_usage_telemetry_false(self):
        s = Settings(
            proxy_api_key="k",
            admin_api_key="k",
            database_url="postgres://x",
            usage_telemetry=False,
        )
        assert s.usage_telemetry is False

    def test_telemetry_endpoint_default(self):
        s = Settings(proxy_api_key="k", admin_api_key="k", database_url="postgres://x")
        assert s.telemetry_endpoint == "https://telemetry.luthien.io/v1/events"

    def test_telemetry_endpoint_override(self):
        s = Settings(
            proxy_api_key="k",
            admin_api_key="k",
            database_url="postgres://x",
            telemetry_endpoint="https://custom.example.com/v1/events",
        )
        assert s.telemetry_endpoint == "https://custom.example.com/v1/events"
