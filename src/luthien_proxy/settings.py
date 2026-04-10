"""Application settings — generated from config_fields.py.

The Settings model is built dynamically from CONFIG_FIELDS so there's exactly
one place to define config fields. Access via get_settings() which returns a
cached singleton.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import create_model, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from luthien_proxy.config_fields import CONFIG_FIELDS


def _build_field_definitions() -> dict[str, Any]:
    """Build pydantic field definitions from CONFIG_FIELDS."""
    fields: dict[str, Any] = {}
    for meta in CONFIG_FIELDS:
        if meta.default is None:
            # Optional field — type is field_type | None
            fields[meta.name] = (meta.field_type | None, None)
        else:
            fields[meta.name] = (meta.field_type, meta.default)
    return fields


class _SettingsBase(BaseSettings):
    """Base with pydantic-settings configuration. Fields added by create_model."""

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


Settings = create_model("Settings", __base__=_SettingsBase, **_build_field_definitions())


@lru_cache
def get_settings() -> Settings:  # type: ignore[valid-type]
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
