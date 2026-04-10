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

from luthien_proxy.config_fields import CONFIG_FIELDS, CONFIG_FIELDS_BY_NAME, ConfigFieldMeta
from luthien_proxy.settings import Settings
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


class ConfigSource(str, Enum):
    """Where a config value came from."""

    CLI = "cli"
    ENV = "env"
    DB = "db"
    DEFAULT = "default"


class ConfigOverriddenError(RuntimeError):
    """Raised when a DB write is attempted on a field currently overridden by CLI/ENV.

    Distinct from ValueError/TypeError so the admin route can map it to 409
    while coercion errors map to 422.
    """

    def __init__(self, name: str, source: ConfigSource) -> None:
        """Build the exception for a field blocked by a higher-priority source."""
        super().__init__(
            f"Config '{name}' is currently overridden by {source.value}; {source.value} takes precedence over DB writes"
        )
        self.name = name
        self.source = source


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
        except Exception:
            # Log at ERROR level so operators notice that runtime config
            # fell back to defaults at startup — this is fail-open behavior
            # and we want it visible, not buried in a warning.
            logger.error(
                "Failed to load gateway_config from DB — registry will use defaults "
                "until a successful reload. Runtime overrides (DB layer) are not applied.",
                exc_info=True,
            )
            return

        loaded: dict[str, str] = {}
        orphan_keys: list[str] = []
        for row in rows:
            key = str(row["key"])
            if key in CONFIG_FIELDS_BY_NAME:
                loaded[key] = str(row["value"])
            else:
                orphan_keys.append(key)
        self._db_values = loaded
        if orphan_keys:
            logger.warning(
                "gateway_config contains %d row(s) for removed/renamed fields (ignored): %s",
                len(orphan_keys),
                ", ".join(sorted(orphan_keys)),
            )

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
            # from_db=True so the JSON-encoded row value is decoded before typing.
            layers[ConfigSource.DB] = coerce_value(meta, self._db_values[meta.name], from_db=True)

        # Resolve by priority
        for source in (ConfigSource.CLI, ConfigSource.ENV, ConfigSource.DB):
            if source in layers:
                overrides = {s.value: v for s, v in layers.items() if s != source}
                return ResolvedValue(value=layers[source], source=source, overrides=overrides)

        return ResolvedValue(
            value=meta.default,
            source=ConfigSource.DEFAULT,
            overrides={s.value: v for s, v in layers.items()},
        )

    def _sync_one(self, name: str, value: Any) -> None:
        """Assign one resolved value back to Settings, logging assignment failures."""
        if not hasattr(self._settings, name):
            return
        try:
            setattr(self._settings, name, value)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to sync config '%s' to Settings: %s. get_settings().%s and registry.get('%s') will diverge.",
                name,
                exc,
                name,
                name,
            )

    def _sync_to_settings(self) -> None:
        """Push resolved values back to Settings singleton for backward compat."""
        for name, resolved in self._resolved.items():
            self._sync_one(name, resolved.value)

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

        Raises:
            ValueError: field is unknown, not db_settable, or no DB is available.
            TypeError / ValueError: coercion of `value` failed.
            ConfigOverriddenError: field is currently overridden by CLI or ENV.
        """
        meta = CONFIG_FIELDS_BY_NAME.get(name)
        if meta is None:
            raise ValueError(f"Unknown config field: {name}")
        if not meta.db_settable:
            raise ValueError(f"Config field '{name}' is not DB-settable")
        if self._db_pool is None:
            raise ValueError("No database connection available")

        # Serialize the coerced (canonical) value so what's stored matches what
        # will be read back. Value comes from user/API so from_db=False (default)
        # — we must not JSON-decode the input, otherwise literal strings "true",
        # "null", "123" for str-typed fields would be corrupted.
        coerced = coerce_value(meta, value)
        serialized = json.dumps(coerced)

        async with self._lock:
            # Check the override under the lock so we can't race against a
            # concurrent cli_overrides mutation between a pre-check and the write.
            current = self._resolved.get(name)
            if current is not None and current.source in (ConfigSource.CLI, ConfigSource.ENV):
                raise ConfigOverriddenError(name, current.source)

            pool = await self._db_pool.get_pool()
            # NOW() is translated to datetime('now') by db_sqlite for SQLite deploys.
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
            self._sync_one(name, resolved.value)

        logger.info("Config '%s' set to %s via DB by %s", name, _display_value(meta, coerced), updated_by)
        return resolved

    async def delete_db_value(self, name: str) -> ResolvedValue:
        """Remove a DB override, falling back to env or default.

        Raises:
            ValueError: field is unknown or no DB is available.
            ConfigOverriddenError: field is currently overridden by CLI or ENV.
                The DB row is NOT removed in this case — the caller intended
                to make the field fall back to a lower layer, and removing the
                row would silently succeed without changing the live value.
        """
        if self._db_pool is None:
            raise ValueError("No database connection available")

        meta = CONFIG_FIELDS_BY_NAME.get(name)
        if meta is None:
            raise ValueError(f"Unknown config field: {name}")

        async with self._lock:
            current = self._resolved.get(name)
            if current is not None and current.source in (ConfigSource.CLI, ConfigSource.ENV):
                raise ConfigOverriddenError(name, current.source)

            pool = await self._db_pool.get_pool()
            await pool.execute("DELETE FROM gateway_config WHERE key = $1", name)
            self._db_values.pop(name, None)

            resolved = self._resolve_field(meta)
            self._resolved[name] = resolved
            self._sync_one(name, resolved.value)

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
                "type": meta.field_type.__name__,
                "value": "***" if meta.sensitive else resolved.value,
                "source": resolved.source.value,
                "default": "***" if meta.sensitive else meta.default,
                "db_settable": meta.db_settable,
                "restart_required": meta.restart_required,
                "sensitive": meta.sensitive,
                "overrides": {k: ("***" if meta.sensitive else v) for k, v in resolved.overrides.items()},
            }
            result.append(entry)
        return result


# ── Helpers ───────────────────────────────────────────────────────────────


def coerce_value(meta: ConfigFieldMeta, raw: Any, *, from_db: bool = False) -> Any:
    """Coerce a raw value to the field's type.

    Args:
        meta: Field definition.
        raw: Raw value from any source (CLI, env, DB row, admin API).
        from_db: True only when called with a value freshly read from the
            gateway_config row — that path unconditionally stores JSON, so
            we decode first. User/CLI input MUST NOT set this flag, otherwise
            the literal string "true" would be silently coerced through a
            JSON decode → bool True → str(True) → "True", corrupting strings.

    Passing raw=None returns None only when the field has default=None (nullable);
    otherwise it raises TypeError since the field type contract demands a value.
    """
    if from_db and isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    if raw is None:
        if meta.default is None:
            return None
        raise TypeError(f"Config field '{meta.name}' ({meta.field_type.__name__}) cannot be None")

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
        return str(raw)
    if isinstance(meta.field_type, type) and issubclass(meta.field_type, Enum):
        # Construct the enum by value (e.g. "passthrough" → AuthMode.PASSTHROUGH).
        if isinstance(raw, meta.field_type):
            return raw
        try:
            return meta.field_type(raw)
        except ValueError as exc:
            valid = ", ".join(repr(m.value) for m in meta.field_type)
            raise ValueError(
                f"Invalid value for '{meta.name}' ({meta.field_type.__name__}): {raw!r} (expected one of: {valid})"
            ) from exc
    return raw


def _display_value(meta: ConfigFieldMeta, value: Any) -> str:
    """Format a value for logging, masking sensitive fields."""
    if meta.sensitive:
        return "***"
    return repr(value)


__all__ = [
    "ConfigOverriddenError",
    "ConfigRegistry",
    "ConfigSource",
    "ResolvedValue",
    "coerce_value",
]
