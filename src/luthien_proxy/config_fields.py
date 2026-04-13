"""Config field definitions — single source of truth for all gateway configuration.

Every config value in the gateway is defined here exactly once. The ConfigFieldMeta
entries drive: Settings model generation, CLI arg generation, .env.example generation,
the config dashboard API, and provenance tracking.

To add a new config value: add a ConfigFieldMeta to CONFIG_FIELDS below.
The Settings model is auto-generated from these definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from luthien_proxy.credential_manager import AuthMode
from luthien_proxy.utils.constants import DEFAULT_GATEWAY_PORT
from luthien_proxy.version import PROXY_VERSION


@dataclass(frozen=True)
class ConfigFieldMeta:
    """Metadata for a single configuration field.

    Attributes:
        name: Python attribute name on Settings (e.g. "gateway_port").
        env_var: Environment variable name (e.g. "GATEWAY_PORT").
        field_type: Python type (int, str, bool, or their Optional variants).
        default: Default value when not set by any source.
        description: Human-readable description (shown in dashboard and .env.example).
        sensitive: If True, value is masked in dashboard/logs.
        category: Grouping key for dashboard display.
        db_settable: If True, can be stored in gateway_config table and edited via admin API.
        restart_required: If True, changes only take effect after gateway restart.
        default_from: Optional (module, symbol) tuple. When set, the generator emits
            `from {module} import {symbol}` in settings.py and uses {symbol} as the
            default expression. Use whenever you want the generated file to reference
            a named constant instead of baking in the literal value.
        dynamic_default: True if the resolved default value depends on the build
            environment and must not be embedded in .env.example (e.g. PROXY_VERSION
            varies between local dev and CI). When set, the env example generator
            emits a blank value with an explanatory comment instead of the stale literal.
    """

    name: str
    env_var: str
    field_type: type
    default: Any
    description: str
    sensitive: bool = False
    category: str = "general"
    db_settable: bool = False
    restart_required: bool = True
    default_from: tuple[str, str] | None = None
    dynamic_default: bool = False


# fmt: off
CONFIG_FIELDS: tuple[ConfigFieldMeta, ...] = (

    # ── server ────────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "gateway_port", "GATEWAY_PORT", int, DEFAULT_GATEWAY_PORT,
        "Port the gateway listens on",
        category="server",
        default_from=("luthien_proxy.utils.constants", "DEFAULT_GATEWAY_PORT"),
    ),
    ConfigFieldMeta(
        "log_level", "LOG_LEVEL", str, "info",
        "Logging level (critical, error, warning, info, debug, trace)",
        category="server", db_settable=True, restart_required=True,
    ),
    ConfigFieldMeta(
        "verbose_client_errors", "VERBOSE_CLIENT_ERRORS", bool, False,
        "Include internal details in client-facing error responses",
        category="server", db_settable=True, restart_required=False,
    ),

    # ── auth ──────────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "client_api_key", "CLIENT_API_KEY", str, None,
        "Shared API key the gateway accepts from clients (optional). Clients set this as ANTHROPIC_API_KEY.",
        sensitive=True, category="auth",
    ),
    ConfigFieldMeta(
        "admin_api_key", "ADMIN_API_KEY", str, None,
        "API key for admin endpoints",
        sensitive=True, category="auth",
    ),
    ConfigFieldMeta(
        "auth_mode", "AUTH_MODE", AuthMode, AuthMode.BOTH,
        "Authentication mode: client_key, passthrough, or both (managed via /api/admin/auth/config)",
        category="auth",
    ),
    ConfigFieldMeta(
        "localhost_auth_bypass", "LOCALHOST_AUTH_BYPASS", bool, True,
        "Skip admin-route authentication for requests from localhost (proxy /v1/messages auth is unaffected; disable behind a same-host reverse proxy)",
        category="auth", db_settable=True, restart_required=False,
    ),

    # ── policy ────────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "policy_source", "POLICY_SOURCE", str, "db-fallback-file",
        "Policy loading strategy: db, file, db-fallback-file, file-fallback-db",
        category="policy",
    ),
    ConfigFieldMeta(
        "policy_config", "POLICY_CONFIG", str, "",
        "Path to policy YAML file",
        category="policy",
    ),
    ConfigFieldMeta(
        "inject_policy_context", "INJECT_POLICY_CONTEXT", bool, True,
        "Inject active policy names into the system message",
        category="policy", db_settable=True, restart_required=False,
    ),
    ConfigFieldMeta(
        "dogfood_mode", "DOGFOOD_MODE", bool, False,
        "Auto-compose DogfoodSafetyPolicy to prevent agents from killing the proxy",
        category="policy", db_settable=True, restart_required=False,
    ),
    ConfigFieldMeta(
        "policy_cache_max_entries", "POLICY_CACHE_MAX_ENTRIES", int, 10_000,
        "Max rows per policy namespace in PolicyCache (0 or negative disables the cap)",
        category="policy",
    ),

    # ── database ──────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "database_url", "DATABASE_URL", str, "",
        "Database connection URL (sqlite:/// or postgres://)",
        sensitive=True, category="database",
    ),
    ConfigFieldMeta(
        "redis_url", "REDIS_URL", str, "",
        "Redis connection URL (optional; enables multi-instance pub/sub) — may contain credentials",
        sensitive=True, category="database",
    ),
    ConfigFieldMeta(
        "migrations_dir", "MIGRATIONS_DIR", str, None,
        "Override path to migrations directory",
        category="database",
    ),

    # ── llm ───────────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "anthropic_api_key", "ANTHROPIC_API_KEY", str, None,
        "Server-side Anthropic API key (optional; enables server-credential mode)",
        sensitive=True, category="llm",
    ),
    ConfigFieldMeta(
        "litellm_master_key", "LITELLM_MASTER_KEY", str, None,
        "LiteLLM master key for multi-tenant deployments",
        sensitive=True, category="llm",
    ),
    ConfigFieldMeta(
        "llm_judge_model", "LLM_JUDGE_MODEL", str, None,
        "Model ID for the LLM judge policy",
        category="llm",
    ),
    ConfigFieldMeta(
        "llm_judge_api_base", "LLM_JUDGE_API_BASE", str, None,
        "Custom API base URL for the judge model",
        category="llm",
    ),
    ConfigFieldMeta(
        "llm_judge_api_key", "LLM_JUDGE_API_KEY", str, None,
        "API key for the judge model",
        sensitive=True, category="llm",
    ),
    ConfigFieldMeta(
        "anthropic_client_cache_size", "ANTHROPIC_CLIENT_CACHE_SIZE", int, 16,
        "Max number of cached Anthropic client instances for passthrough auth",
        category="llm",
    ),

    # ── security ──────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "credential_encryption_key", "CREDENTIAL_ENCRYPTION_KEY", str, None,
        "Fernet key for encrypting server credentials at rest",
        sensitive=True, category="security",
    ),

    # ── observability ─────────────────────────────────────────────────────
    ConfigFieldMeta(
        "otel_enabled", "OTEL_ENABLED", bool, False,
        "Enable OpenTelemetry distributed tracing",
        category="observability",
    ),
    ConfigFieldMeta(
        "otel_exporter_otlp_endpoint", "OTEL_EXPORTER_OTLP_ENDPOINT", str, "http://tempo:4317",
        "OTLP exporter endpoint for traces",
        category="observability",
    ),
    ConfigFieldMeta(
        "tempo_url", "TEMPO_URL", str, "http://localhost:3200",
        "Tempo HTTP API URL for trace queries",
        category="observability",
    ),
    ConfigFieldMeta(
        "service_name", "SERVICE_NAME", str, "luthien-proxy",
        "Service name for distributed tracing",
        category="observability",
    ),
    ConfigFieldMeta(
        "service_version", "SERVICE_VERSION", str, PROXY_VERSION,
        "Service version for distributed tracing (derived from package metadata)",
        category="observability",
        default_from=("luthien_proxy.version", "PROXY_VERSION"),
        dynamic_default=True,
    ),
    ConfigFieldMeta(
        "environment", "ENVIRONMENT", str, "development",
        "Environment name (development, staging, production)",
        category="observability",
    ),
    ConfigFieldMeta(
        "railway_service_name", "RAILWAY_SERVICE_NAME", str, "",
        "Railway service name (auto-sets environment if present)",
        category="observability",
    ),
    ConfigFieldMeta(
        "enable_request_logging", "ENABLE_REQUEST_LOGGING", bool, False,
        "Log full HTTP request and response bodies",
        category="observability",
    ),

    # ── telemetry ─────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "usage_telemetry", "USAGE_TELEMETRY", bool, None,
        "Anonymous usage telemetry (None defers to DB config, True/False overrides)",
        category="telemetry", db_settable=True, restart_required=False,
    ),
    ConfigFieldMeta(
        "telemetry_endpoint", "TELEMETRY_ENDPOINT", str, "https://telemetry.luthien.cc/v1/events",
        "Endpoint for anonymous usage metrics",
        category="telemetry",
    ),

    # ── webhook ───────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "webhook_url", "WEBHOOK_URL", str, "",
        "Endpoint URL to POST conversation completion events to (leave empty to disable)",
        category="webhook",
    ),
    ConfigFieldMeta(
        "webhook_max_retries", "WEBHOOK_MAX_RETRIES", int, 3,
        "Number of retry attempts for failed webhook deliveries",
        category="webhook",
    ),
    ConfigFieldMeta(
        "webhook_retry_delay_seconds", "WEBHOOK_RETRY_DELAY_SECONDS", float, 1.0,
        "Base delay in seconds between webhook retry attempts (doubles each retry)",
        category="webhook",
    ),

    # ── sentry ────────────────────────────────────────────────────────────
    ConfigFieldMeta(
        "sentry_enabled", "SENTRY_ENABLED", bool, False,
        "Enable Sentry error tracking",
        category="sentry",
    ),
    ConfigFieldMeta(
        "sentry_dsn", "SENTRY_DSN", str, "",
        "Sentry project DSN",
        sensitive=True, category="sentry",
    ),
    ConfigFieldMeta(
        "sentry_traces_sample_rate", "SENTRY_TRACES_SAMPLE_RATE", float, 0.0,
        "Sentry traces sampling rate (0.0 to 1.0)",
        category="sentry",
    ),
    ConfigFieldMeta(
        "sentry_server_name", "SENTRY_SERVER_NAME", str, "",
        "Sentry server identifier",
        category="sentry",
    ),
)
# fmt: on


# Index for O(1) lookup by name
CONFIG_FIELDS_BY_NAME: dict[str, ConfigFieldMeta] = {f.name: f for f in CONFIG_FIELDS}

# Ordered list of categories for dashboard display
CONFIG_CATEGORIES: tuple[str, ...] = (
    "server",
    "auth",
    "policy",
    "database",
    "llm",
    "security",
    "observability",
    "telemetry",
    "webhook",
    "sentry",
)


__all__ = [
    "ConfigFieldMeta",
    "CONFIG_FIELDS",
    "CONFIG_FIELDS_BY_NAME",
    "CONFIG_CATEGORIES",
]
