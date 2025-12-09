"""Unit tests for Settings class and configuration management."""

import pytest

from luthien_proxy.settings import Settings, clear_settings_cache, get_settings


class TestSettingsDefaults:
    """Test default values for settings.

    Note: Some defaults may be overridden by conftest.py for test isolation.
    Tests here verify the Settings class behavior, not conftest defaults.
    """

    def test_default_redis_url(self, monkeypatch):
        """Test default Redis URL for local development."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        settings = Settings()
        assert settings.redis_url == "redis://localhost:6379"

    def test_default_policy_config(self, monkeypatch):
        """Test default policy config path."""
        monkeypatch.delenv("POLICY_CONFIG", raising=False)
        settings = Settings()
        assert settings.policy_config == "config/policy_config.yaml"

    def test_default_policy_source(self, monkeypatch):
        """Test default policy source."""
        monkeypatch.delenv("POLICY_SOURCE", raising=False)
        settings = Settings()
        assert settings.policy_source == "db-fallback-file"

    def test_default_otel_enabled(self, monkeypatch):
        """Test OpenTelemetry is enabled by default."""
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        settings = Settings()
        assert settings.otel_enabled is True

    def test_default_service_name(self, monkeypatch):
        """Test default service name."""
        monkeypatch.delenv("SERVICE_NAME", raising=False)
        settings = Settings()
        assert settings.service_name == "luthien-proxy"

    def test_default_grafana_url(self, monkeypatch):
        """Test default Grafana URL."""
        monkeypatch.delenv("GRAFANA_URL", raising=False)
        settings = Settings()
        assert settings.grafana_url == "http://localhost:3000"

    def test_optional_fields_default_to_none(self, monkeypatch):
        """Test optional fields default to None."""
        for var in [
            "PROXY_API_KEY",
            "ADMIN_API_KEY",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "LLM_JUDGE_MODEL",
            "LLM_JUDGE_API_BASE",
            "LLM_JUDGE_API_KEY",
            "LITELLM_MASTER_KEY",
        ]:
            monkeypatch.delenv(var, raising=False)
        settings = Settings()
        assert settings.proxy_api_key is None
        assert settings.admin_api_key is None
        assert settings.otel_exporter_otlp_endpoint is None
        assert settings.llm_judge_model is None
        assert settings.llm_judge_api_base is None
        assert settings.llm_judge_api_key is None
        assert settings.litellm_master_key is None


class TestSettingsFromEnv:
    """Test loading settings from environment variables."""

    def test_loads_proxy_api_key(self, monkeypatch):
        """Test PROXY_API_KEY is loaded from environment."""
        monkeypatch.setenv("PROXY_API_KEY", "test-key-123")
        settings = Settings()
        assert settings.proxy_api_key == "test-key-123"

    def test_loads_database_url(self, monkeypatch):
        """Test DATABASE_URL is loaded from environment."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        settings = Settings()
        assert settings.database_url == "postgresql://user:pass@localhost/db"

    def test_loads_redis_url(self, monkeypatch):
        """Test REDIS_URL overrides default."""
        monkeypatch.setenv("REDIS_URL", "redis://custom:6380")
        settings = Settings()
        assert settings.redis_url == "redis://custom:6380"

    def test_loads_otel_enabled_false(self, monkeypatch):
        """Test OTEL_ENABLED can be set to false."""
        monkeypatch.setenv("OTEL_ENABLED", "false")
        settings = Settings()
        assert settings.otel_enabled is False

    def test_loads_policy_source(self, monkeypatch):
        """Test POLICY_SOURCE is loaded from environment."""
        monkeypatch.setenv("POLICY_SOURCE", "file")
        settings = Settings()
        assert settings.policy_source == "file"


class TestPolicySourceValidation:
    """Test Literal type validation for policy_source."""

    def test_valid_policy_source_db(self, monkeypatch):
        """Test valid policy source: db."""
        monkeypatch.setenv("POLICY_SOURCE", "db")
        settings = Settings()
        assert settings.policy_source == "db"

    def test_valid_policy_source_file(self, monkeypatch):
        """Test valid policy source: file."""
        monkeypatch.setenv("POLICY_SOURCE", "file")
        settings = Settings()
        assert settings.policy_source == "file"

    def test_valid_policy_source_db_fallback_file(self, monkeypatch):
        """Test valid policy source: db-fallback-file."""
        monkeypatch.setenv("POLICY_SOURCE", "db-fallback-file")
        settings = Settings()
        assert settings.policy_source == "db-fallback-file"

    def test_valid_policy_source_file_fallback_db(self, monkeypatch):
        """Test valid policy source: file-fallback-db."""
        monkeypatch.setenv("POLICY_SOURCE", "file-fallback-db")
        settings = Settings()
        assert settings.policy_source == "file-fallback-db"

    def test_invalid_policy_source_raises_error(self, monkeypatch):
        """Test invalid POLICY_SOURCE raises validation error."""
        monkeypatch.setenv("POLICY_SOURCE", "invalid-source")
        with pytest.raises(Exception):  # Pydantic ValidationError
            Settings()


class TestEffectiveOtelEndpoint:
    """Test the effective_otel_endpoint property."""

    def test_uses_standard_endpoint_when_set(self, monkeypatch):
        """Test OTEL_EXPORTER_OTLP_ENDPOINT takes precedence."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://custom:4317")
        settings = Settings()
        assert settings.effective_otel_endpoint == "http://custom:4317"

    def test_falls_back_to_legacy_endpoint(self, monkeypatch):
        """Test falls back to OTEL_ENDPOINT when standard not set."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("OTEL_ENDPOINT", "http://legacy:4317")
        settings = Settings()
        assert settings.effective_otel_endpoint == "http://legacy:4317"

    def test_uses_default_when_neither_set(self, monkeypatch):
        """Test uses default OTEL_ENDPOINT when neither env var set."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_ENDPOINT", raising=False)
        settings = Settings()
        assert settings.effective_otel_endpoint == "http://tempo:4317"


class TestSettingsCache:
    """Test settings caching behavior."""

    def test_get_settings_returns_same_instance(self):
        """Test get_settings returns cached instance."""
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2

    def test_clear_settings_cache_clears_cache(self, monkeypatch):
        """Test clear_settings_cache creates fresh instance."""
        # Get initial settings
        settings1 = get_settings()
        initial_redis = settings1.redis_url

        # Clear cache and change env
        clear_settings_cache()
        monkeypatch.setenv("REDIS_URL", "redis://different:6379")

        # Get new settings
        settings2 = get_settings()

        # Settings should be different objects with different values
        assert settings1 is not settings2
        assert settings2.redis_url == "redis://different:6379"
        assert initial_redis != settings2.redis_url
