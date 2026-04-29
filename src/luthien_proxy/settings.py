"""Application settings — generated from config_fields.py.

DO NOT EDIT BY HAND. Regenerate with:
    uv run python scripts/generate_settings.py

The Settings model is a plain class with explicit typed fields so Pyright
can check attribute access. config_fields.py remains the single source of truth.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from luthien_proxy.credential_manager import AuthMode
from luthien_proxy.utils.constants import DEFAULT_GATEWAY_PORT
from luthien_proxy.version import PROXY_VERSION


class _SettingsBase(BaseSettings):
    """Base with pydantic-settings configuration. Fields declared on Settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _set_environment_from_railway(self) -> "_SettingsBase":
        """Auto-set environment from Railway service name."""
        railway = getattr(self, "railway_service_name", "")
        env = getattr(self, "environment", "development")
        if railway and env == "development":
            object.__setattr__(self, "environment", railway)
        return self


class Settings(_SettingsBase):
    """Application settings — all fields generated from config_fields.py."""

    # ── server ──────────────────────────────────────────────────────
    gateway_port: int = DEFAULT_GATEWAY_PORT
    log_level: str = "info"
    verbose_client_errors: bool = False
    trust_user_id_header: bool = False

    # ── auth ────────────────────────────────────────────────────────
    client_api_key: str | None = None
    admin_api_key: str | None = None
    auth_mode: AuthMode = AuthMode.BOTH
    localhost_auth_bypass: bool = True

    # ── policy ──────────────────────────────────────────────────────
    policy_source: str = "db-fallback-file"
    policy_config: str = ""
    inject_policy_context: bool = True
    dogfood_mode: bool = False
    policy_cache_max_entries: int = 10000

    # ── database ────────────────────────────────────────────────────
    database_url: str = ""
    redis_url: str = ""
    migrations_dir: str | None = None

    # ── llm ─────────────────────────────────────────────────────────
    anthropic_api_key: str | None = None
    llm_judge_model: str | None = None
    llm_judge_api_base: str | None = None
    anthropic_client_cache_size: int = 16

    # ── security ────────────────────────────────────────────────────
    credential_encryption_key: str | None = None

    # ── observability ───────────────────────────────────────────────
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://tempo:4317"
    otel_exporter_protocol: str = Field(default="http/protobuf", validation_alias="OTEL_EXPORTER_OTLP_PROTOCOL")
    tempo_url: str = "http://localhost:3200"
    service_name: str = "luthien-proxy"
    service_version: str = PROXY_VERSION
    environment: str = "development"
    railway_service_name: str = ""
    enable_request_logging: bool = False

    # ── telemetry ───────────────────────────────────────────────────
    usage_telemetry: bool | None = None
    telemetry_endpoint: str = "https://telemetry.luthien.cc/v1/events"

    # ── retention ───────────────────────────────────────────────────
    conversation_retention_days: int | None = None
    archive_s3_bucket: str | None = None
    archive_s3_prefix: str = "luthien-archive/"
    retention_archive_batch_size: int = 1000

    # ── webhook ─────────────────────────────────────────────────────
    webhook_url: str = ""
    webhook_max_retries: int = 3
    webhook_retry_delay_seconds: float = 1.0
    webhook_max_pending_tasks: int = 1000

    # ── sentry ──────────────────────────────────────────────────────
    sentry_enabled: bool = False
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    sentry_server_name: str = ""


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear the settings cache. Useful for testing."""
    get_settings.cache_clear()


def client_error_detail(verbose_detail: str, generic_detail: str = "Internal server error") -> str:
    """Pick the client-facing error message based on VERBOSE_CLIENT_ERRORS."""
    return verbose_detail if get_settings().verbose_client_errors else generic_detail


__all__ = ["Settings", "get_settings", "clear_settings_cache", "client_error_detail"]
