"""Centralized environment configuration using pydantic-settings.

All environment variables should be defined here for type safety, validation,
and discoverability. Access settings via get_settings() which returns a cached
Settings instance.

Defaults are pulled from config_fields.py (the single source of truth for
config metadata). Types here may be richer than config_fields (e.g. AuthMode
enum vs plain str) — that's intentional, as Settings is the typed runtime
interface.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from luthien_proxy.config_fields import CONFIG_FIELDS_BY_NAME as _F
from luthien_proxy.credential_manager import AuthMode


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings are optional with sensible defaults for local development.
    Required settings (like PROXY_API_KEY) will raise validation errors if not set
    when accessed in production contexts.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults sourced from CONFIG_FIELDS — the single source of truth.
    # Types here are the typed runtime interface (may be richer than config_fields).

    proxy_api_key: str | None = _F["proxy_api_key"].default
    admin_api_key: str | None = _F["admin_api_key"].default
    auth_mode: AuthMode = AuthMode.BOTH

    gateway_port: int = _F["gateway_port"].default

    database_url: str = _F["database_url"].default
    redis_url: str = _F["redis_url"].default

    policy_source: str = _F["policy_source"].default
    policy_config: str = _F["policy_config"].default

    otel_enabled: bool = _F["otel_enabled"].default
    otel_exporter_otlp_endpoint: str = _F["otel_exporter_otlp_endpoint"].default
    tempo_url: str = _F["tempo_url"].default
    service_name: str = _F["service_name"].default
    service_version: str = _F["service_version"].default
    environment: str = _F["environment"].default
    railway_service_name: str = _F["railway_service_name"].default

    @model_validator(mode="after")
    def _set_environment_from_railway(self) -> "Settings":
        if self.railway_service_name and self.environment == "development":
            self.environment = self.railway_service_name
        return self

    enable_request_logging: bool = _F["enable_request_logging"].default

    usage_telemetry: bool | None = _F["usage_telemetry"].default
    telemetry_endpoint: str = _F["telemetry_endpoint"].default

    llm_judge_model: str | None = _F["llm_judge_model"].default
    llm_judge_api_base: str | None = _F["llm_judge_api_base"].default
    llm_judge_api_key: str | None = _F["llm_judge_api_key"].default
    litellm_master_key: str | None = _F["litellm_master_key"].default

    credential_encryption_key: str | None = _F["credential_encryption_key"].default

    localhost_auth_bypass: bool = _F["localhost_auth_bypass"].default
    inject_policy_context: bool = _F["inject_policy_context"].default
    dogfood_mode: bool = _F["dogfood_mode"].default
    verbose_client_errors: bool = _F["verbose_client_errors"].default

    sentry_enabled: bool = _F["sentry_enabled"].default
    sentry_dsn: str = _F["sentry_dsn"].default
    sentry_traces_sample_rate: float = _F["sentry_traces_sample_rate"].default
    sentry_server_name: str = _F["sentry_server_name"].default

    log_level: str = _F["log_level"].default
    anthropic_api_key: str | None = _F["anthropic_api_key"].default
    anthropic_client_cache_size: int = _F["anthropic_client_cache_size"].default
    migrations_dir: str | None = _F["migrations_dir"].default


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Returns:
        Cached Settings instance loaded from environment
    """
    return Settings()


def clear_settings_cache() -> None:
    """Clear the settings cache. Useful for testing."""
    get_settings.cache_clear()


def client_error_detail(verbose_detail: str, generic_detail: str = "Internal server error") -> str:
    """Pick the client-facing error message based on VERBOSE_CLIENT_ERRORS."""
    return verbose_detail if get_settings().verbose_client_errors else generic_detail


__all__ = ["Settings", "get_settings", "clear_settings_cache", "client_error_detail"]
