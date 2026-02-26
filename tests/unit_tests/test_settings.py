"""Unit tests for Settings class and configuration management."""

import inspect
import re
from pathlib import Path

import pytest

from luthien_proxy.credential_manager import AuthMode, CredentialManager
from luthien_proxy.settings import Settings, clear_settings_cache, get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class TestSettingsDefaults:
    """Test default values for settings.

    These tests use _env_file=None to bypass .env file loading and test
    the actual class defaults in isolation from the local .env file.
    """

    def test_default_redis_url(self, monkeypatch):
        """Test default Redis URL for local development."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.redis_url == "redis://localhost:6379"

    def test_default_policy_config(self, monkeypatch):
        """Test default policy config path is empty (load from DB)."""
        monkeypatch.delenv("POLICY_CONFIG", raising=False)
        settings = Settings(_env_file=None)
        assert settings.policy_config == ""

    def test_default_otel_enabled(self, monkeypatch):
        """Test OpenTelemetry is enabled by default."""
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        settings = Settings(_env_file=None)
        assert settings.otel_enabled is True

    def test_default_service_name(self, monkeypatch):
        """Test default service name."""
        monkeypatch.delenv("SERVICE_NAME", raising=False)
        settings = Settings(_env_file=None)
        assert settings.service_name == "luthien-proxy"

    def test_default_gateway_port(self, monkeypatch):
        """Test default gateway port is 8000."""
        monkeypatch.delenv("GATEWAY_PORT", raising=False)
        settings = Settings(_env_file=None)
        assert settings.gateway_port == 8000

    def test_default_tempo_url(self, monkeypatch):
        """Test default Tempo URL for local development."""
        monkeypatch.delenv("TEMPO_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.tempo_url == "http://localhost:3200"

    def test_optional_fields_default_to_none(self, monkeypatch):
        """Test optional fields default to None."""
        for var in [
            "PROXY_API_KEY",
            "ADMIN_API_KEY",
            "LLM_JUDGE_MODEL",
            "LLM_JUDGE_API_BASE",
            "LLM_JUDGE_API_KEY",
            "LITELLM_MASTER_KEY",
        ]:
            monkeypatch.delenv(var, raising=False)
        settings = Settings(_env_file=None)
        assert settings.proxy_api_key is None
        assert settings.admin_api_key is None
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

    def test_loads_policy_config(self, monkeypatch):
        """Test POLICY_CONFIG is loaded from environment."""
        monkeypatch.setenv("POLICY_CONFIG", "custom/policy.yaml")
        settings = Settings()
        assert settings.policy_config == "custom/policy.yaml"

    def test_loads_gateway_port(self, monkeypatch):
        """Test GATEWAY_PORT is loaded from environment."""
        monkeypatch.setenv("GATEWAY_PORT", "3000")
        settings = Settings()
        assert settings.gateway_port == 3000

    def test_loads_tempo_url(self, monkeypatch):
        """Test TEMPO_URL overrides default."""
        monkeypatch.setenv("TEMPO_URL", "http://tempo.prod:3200")
        settings = Settings()
        assert settings.tempo_url == "http://tempo.prod:3200"


class TestOtelExporterEndpoint:
    """Test the otel_exporter_otlp_endpoint setting."""

    def test_uses_env_var_when_set(self, monkeypatch):
        """Test OTEL_EXPORTER_OTLP_ENDPOINT is loaded from environment."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://custom:4317")
        settings = Settings(_env_file=None)
        assert settings.otel_exporter_otlp_endpoint == "http://custom:4317"

    def test_uses_default_when_not_set(self, monkeypatch):
        """Test uses default when env var not set."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        settings = Settings(_env_file=None)
        assert settings.otel_exporter_otlp_endpoint == "http://tempo:4317"


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


class TestAuthModeValidation:
    """Test that auth_mode uses AuthMode enum for early validation."""

    def test_default_auth_mode(self, monkeypatch):
        monkeypatch.delenv("AUTH_MODE", raising=False)
        settings = Settings(_env_file=None)
        assert settings.auth_mode == AuthMode.BOTH

    def test_valid_auth_mode_from_env(self, monkeypatch):
        monkeypatch.setenv("AUTH_MODE", "passthrough")
        settings = Settings(_env_file=None)
        assert settings.auth_mode == AuthMode.PASSTHROUGH

    def test_invalid_auth_mode_raises(self, monkeypatch):
        monkeypatch.setenv("AUTH_MODE", "invalid_mode")
        with pytest.raises(Exception):
            Settings(_env_file=None)


class TestAuthModeDefaultConsistency:
    """Ensure DB migration default and Python defaults never silently diverge.

    PR #222 COE: migration 007 seeded 'proxy_key' while settings.py defaulted
    to 'both', causing 401s for Claude Code OAuth. This test catches that class
    of bug by reading the actual migration SQL and comparing to Python defaults.
    """

    def _get_effective_db_default(self) -> str:
        """Read migrations to determine the effective DB default for auth_mode.

        Applies migrations in order: starts with 007's CREATE TABLE DEFAULT,
        then checks if any later migration ALTERs it.
        """
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        db_default = None

        for migration_file in migration_files:
            sql = migration_file.read_text()

            # Look for CREATE TABLE default: DEFAULT 'value'
            create_match = re.search(
                r"auth_mode\s+TEXT\s+NOT\s+NULL\s+DEFAULT\s+'(\w+)'",
                sql,
                re.IGNORECASE,
            )
            if create_match:
                db_default = create_match.group(1)

            # Look for ALTER TABLE default: SET DEFAULT 'value'
            alter_match = re.search(
                r"ALTER\s+TABLE\s+auth_config\s+ALTER\s+COLUMN\s+auth_mode\s+SET\s+DEFAULT\s+'(\w+)'",
                sql,
                re.IGNORECASE,
            )
            if alter_match:
                db_default = alter_match.group(1)

        assert db_default is not None, "No auth_mode default found in migrations"
        return db_default

    def test_migration_default_matches_settings(self, monkeypatch):
        """DB migration default must match Settings.auth_mode default."""
        monkeypatch.delenv("AUTH_MODE", raising=False)
        settings_default = Settings(_env_file=None).auth_mode.value
        db_default = self._get_effective_db_default()
        assert db_default == settings_default, (
            f"DB migration seeds auth_mode='{db_default}' but Settings defaults "
            f"to '{settings_default}'. These must match or dogfooding breaks. "
            f"See PR #222 COE."
        )

    def test_credential_manager_initialize_default_matches_settings(self, monkeypatch):
        """CredentialManager.initialize() fallback must match Settings default."""
        monkeypatch.delenv("AUTH_MODE", raising=False)
        settings_default = Settings(_env_file=None).auth_mode.value
        sig = inspect.signature(CredentialManager.initialize)
        init_default = sig.parameters["default_auth_mode"].default
        assert init_default == settings_default, (
            f"CredentialManager.initialize() defaults to '{init_default}' but "
            f"Settings defaults to '{settings_default}'. These must match. "
            f"See PR #222 COE."
        )
