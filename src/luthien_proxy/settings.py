"""Centralized environment configuration using pydantic-settings.

All environment variables should be defined here for type safety, validation,
and discoverability. Access settings via get_settings() which returns a cached
Settings instance.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from luthien_proxy.credential_manager import AuthMode
from luthien_proxy.utils.constants import DEFAULT_GATEWAY_PORT


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

    # Core API keys
    proxy_api_key: str | None = None
    admin_api_key: str | None = None
    auth_mode: AuthMode = AuthMode.BOTH

    # Server configuration
    gateway_port: int = Field(default=DEFAULT_GATEWAY_PORT)

    # Database and Redis
    database_url: str = ""
    redis_url: str = "redis://localhost:6379"

    # Policy configuration
    policy_source: str = "db-fallback-file"
    policy_config: str = ""

    # OpenTelemetry / Observability
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://tempo:4317"
    tempo_url: str = "http://localhost:3200"
    service_name: str = "luthien-proxy"
    service_version: str = "2.0.0"
    environment: str = "development"

    # Request/response logging
    enable_request_logging: bool = False

    # LLM Judge policy configuration
    llm_judge_model: str | None = None
    llm_judge_api_base: str | None = None
    llm_judge_api_key: str | None = None
    litellm_master_key: str | None = None

    # Dogfood mode — auto-compose DogfoodSafetyPolicy to prevent agents
    # from killing the proxy they communicate through
    dogfood_mode: bool = False


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


__all__ = ["Settings", "get_settings", "clear_settings_cache"]
