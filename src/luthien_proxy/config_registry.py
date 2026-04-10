"""Unified config resolution engine with provenance tracking.

Resolves configuration values through a layered priority chain:
    CLI args > environment variables > database (gateway_config) > defaults

Each resolved value tracks which source provided it and what values from
lower-priority sources were overridden — powering the config dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from luthien_proxy.config_fields import CONFIG_CATEGORIES, CONFIG_FIELDS, CONFIG_FIELDS_BY_NAME, ConfigFieldMeta
from luthien_proxy.settings import Settings
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


class ConfigSource(str, Enum):
    """Where a config value came from."""

    CLI = "cli"
    ENV = "env"
    DB = "db"
    DEFAULT = "default"


@dataclass
class ResolvedValue:
    """A resolved config value with provenance information."""

    value: Any
    source: ConfigSource
    overrides: dict[str, Any] = field(default_factory=dict)


class ConfigRegistry:
    """Resolves config values from CLI > env > DB > defaults with provenance tracking.

    The registry wraps the existing Settings object (for env var resolution) and
    adds CLI and DB layers on top. It also syncs resolved values back to the
    Settings singleton for backward compatibility with code using get_settings().
    """

    def __init__(
        self,
        settings: Settings,
        db_pool: DatabasePool | None,
        cli_overrides: dict[str, Any] | None = None,
    ) -> None:
        """Initialize with a Settings instance, optional DB pool, and CLI overrides."""
        self._settings = settings
        self._db_pool = db_pool
        self._cli_overrides = cli_overrides or {}
        self._db_values: dict[str, str] = {}
        self._resolved: dict[str, ResolvedValue] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Load DB values and resolve all fields."""
        await self._load_db_values()
        self._resolve_all()
        self._sync_to_settings()

    async def _load_db_values(self) -> None:
        if self._db_pool is None:
            return
        try:
            pool = await self._db_pool.get_pool()
            rows = await pool.fetch("SELECT key, value FROM gateway_config")
            self._db_values = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception:
            logger.warning("Failed to load gateway_config from DB", exc_info=True)

    def _resolve_all(self) -> None:
        for meta in CONFIG_FIELDS:
            self._resolved[meta.name] = self._resolve_field(meta)

    def _resolve_field(self, meta: ConfigFieldMeta) -> ResolvedValue:
        layers: dict[ConfigSource, Any] = {}

        # CLI layer (highest priority)
        cli_val = self._cli_overrides.get(meta.name)
        if cli_val is not None:
            layers[ConfigSource.CLI] = cli_val

        # ENV layer — counts if the env var is explicitly set OR the .env file
        # provided a value (detected by comparing Settings value to the field default).
        # pydantic-settings reads .env files directly without injecting into os.environ.
        settings_val = getattr(self._settings, meta.name, meta.default)
        env_explicitly_set = os.environ.get(meta.env_var) is not None
        env_from_dotenv = not env_explicitly_set and settings_val != meta.default
        if env_explicitly_set or env_from_dotenv:
            layers[ConfigSource.ENV] = settings_val

        # DB layer — only for db_settable fields
        if meta.db_settable and meta.name in self._db_values:
            layers[ConfigSource.DB] = _coerce(meta, self._db_values[meta.name])

        # Resolve by priority
        for source in (ConfigSource.CLI, ConfigSource.ENV, ConfigSource.DB):
            if source in layers:
                overrides = {_source_key(s): v for s, v in layers.items() if s != source}
                return ResolvedValue(value=layers[source], source=source, overrides=overrides)

        return ResolvedValue(
            value=meta.default,
            source=ConfigSource.DEFAULT,
            overrides={_source_key(s): v for s, v in layers.items()},
        )

    def _sync_to_settings(self) -> None:
        """Push resolved values back to Settings singleton for backward compat."""
        for name, resolved in self._resolved.items():
            if hasattr(self._settings, name):
                try:
                    setattr(self._settings, name, resolved.value)
                except (AttributeError, TypeError):
                    pass

    # ── Public API ────────────────────────────────────────────────────────

    def get(self, name: str) -> Any:
        """Get the resolved value for a config field."""
        return self._resolved[name].value

    def get_resolved(self, name: str) -> ResolvedValue:
        """Get the full resolved value with provenance."""
        return self._resolved[name]

    def get_field_meta(self, name: str) -> ConfigFieldMeta | None:
        """Get field metadata by name."""
        return CONFIG_FIELDS_BY_NAME.get(name)

    async def set_db_value(self, name: str, value: Any, *, updated_by: str = "admin-api") -> ResolvedValue:
        """Write a value to the gateway_config DB table and update in-memory state.

        Raises ValueError if the field is not db_settable or no DB is available.
        """
        meta = CONFIG_FIELDS_BY_NAME.get(name)
        if meta is None:
            raise ValueError(f"Unknown config field: {name}")
        if not meta.db_settable:
            raise ValueError(f"Config field '{name}' is not DB-settable")
        if self._db_pool is None:
            raise ValueError("No database connection available")

        coerced = _coerce(meta, value)
        serialized = json.dumps(value)

        async with self._lock:
            pool = await self._db_pool.get_pool()
            await pool.execute(
                """
                INSERT INTO gateway_config (key, value, updated_at, updated_by)
                VALUES ($1, $2, NOW(), $3)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                name,
                serialized,
                updated_by,
            )

            self._db_values[name] = serialized
            resolved = self._resolve_field(meta)
            self._resolved[name] = resolved
            if hasattr(self._settings, name):
                try:
                    setattr(self._settings, name, resolved.value)
                except (AttributeError, TypeError):
                    pass

        logger.info(f"Config '{name}' set to {_display_value(meta, coerced)} via DB by {updated_by}")
        return resolved

    async def delete_db_value(self, name: str) -> ResolvedValue:
        """Remove a DB override, falling back to env or default."""
        if self._db_pool is None:
            raise ValueError("No database connection available")

        meta = CONFIG_FIELDS_BY_NAME.get(name)
        if meta is None:
            raise ValueError(f"Unknown config field: {name}")

        async with self._lock:
            pool = await self._db_pool.get_pool()
            await pool.execute("DELETE FROM gateway_config WHERE key = $1", name)
            self._db_values.pop(name, None)

            resolved = self._resolve_field(meta)
            self._resolved[name] = resolved
            if hasattr(self._settings, name):
                try:
                    setattr(self._settings, name, resolved.value)
                except (AttributeError, TypeError):
                    pass

        return resolved

    def dashboard_view(self) -> list[dict[str, Any]]:
        """All fields with metadata and provenance, for the config dashboard API."""
        result: list[dict[str, Any]] = []
        for meta in CONFIG_FIELDS:
            resolved = self._resolved.get(meta.name, ResolvedValue(meta.default, ConfigSource.DEFAULT))
            entry: dict[str, Any] = {
                "name": meta.name,
                "env_var": meta.env_var,
                "category": meta.category,
                "description": meta.description,
                "type": meta.field_type.__name__ if meta.field_type is not type(None) else "NoneType",
                "value": "***" if meta.sensitive else resolved.value,
                "source": resolved.source.value,
                "default": meta.default,
                "db_settable": meta.db_settable,
                "restart_required": meta.restart_required,
                "sensitive": meta.sensitive,
                "overrides": {k: ("***" if meta.sensitive else v) for k, v in resolved.overrides.items()},
            }
            result.append(entry)
        return result

    def generate_env_example(self) -> str:
        """Generate .env.example content from field definitions."""
        lines = [
            "# Luthien Proxy — Environment Configuration",
            "# Auto-generated from config field definitions.",
            "# Copy to .env and edit as needed.",
            "",
        ]
        current_category: str | None = None

        fields_by_cat: dict[str, list[ConfigFieldMeta]] = {}
        for meta in CONFIG_FIELDS:
            fields_by_cat.setdefault(meta.category, []).append(meta)

        for cat in CONFIG_CATEGORIES:
            cat_fields = fields_by_cat.get(cat, [])
            if not cat_fields:
                continue
            if current_category is not None:
                lines.append("")
            current_category = cat
            lines.append(f"# {'═' * 3} {cat.upper()} {'═' * (60 - len(cat))}")
            lines.append("")

            for meta in cat_fields:
                lines.append(f"# {meta.description}")
                if meta.sensitive:
                    lines.append("# (sensitive — not shown in config dashboard)")
                if meta.db_settable:
                    lines.append("# (DB-settable — can be changed at runtime via admin API)")

                default_str = "" if meta.default is None else str(meta.default)
                if isinstance(meta.default, bool):
                    default_str = str(meta.default).lower()
                lines.append(f"# {meta.env_var}={default_str}")
                lines.append("")

        return "\n".join(lines) + "\n"


# ── Helpers ───────────────────────────────────────────────────────────────


def _source_key(source: ConfigSource) -> str:
    """Get the string key for a ConfigSource (its enum value, e.g. 'cli', 'env')."""
    return source.value  # type: ignore[return-value]


def _coerce(meta: ConfigFieldMeta, raw: Any) -> Any:
    """Coerce a raw value (possibly JSON string from DB) to the field's type."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    if meta.field_type is bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            lower = raw.lower()
            if lower in ("true", "1", "yes"):
                return True
            if lower in ("false", "0", "no"):
                return False
            raise ValueError(f"Invalid boolean value for '{meta.name}': {raw!r} (expected true/false/yes/no/1/0)")
        return bool(raw)
    if meta.field_type is int:
        return int(raw)
    if meta.field_type is float:
        return float(raw)
    if meta.field_type is str:
        return str(raw) if raw is not None else None
    return raw


def _display_value(meta: ConfigFieldMeta, value: Any) -> str:
    """Format a value for logging, masking sensitive fields."""
    if meta.sensitive:
        return "***"
    return repr(value)


__all__ = ["ConfigRegistry", "ConfigSource", "ResolvedValue"]
