"""Luthien - integrated FastAPI + LiteLLM proxy with OpenTelemetry observability."""

from __future__ import annotations

import argparse
import enum
import logging
import os
import secrets
from collections.abc import MutableMapping
from contextlib import asynccontextmanager

import litellm
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware

from luthien_proxy.admin import router as admin_router
from luthien_proxy.config_fields import CONFIG_FIELDS, CONFIG_FIELDS_BY_NAME
from luthien_proxy.config_registry import ConfigRegistry, coerce_value
from luthien_proxy.credential_manager import AuthMode, CredentialManager
from luthien_proxy.debug import router as debug_router
from luthien_proxy.dependencies import Dependencies
from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.gateway_routes import _passthrough_client
from luthien_proxy.gateway_routes import router as gateway_router
from luthien_proxy.history import routes as history_routes
from luthien_proxy.llm import anthropic_client_cache
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.observability.emitter import EventEmitter
from luthien_proxy.observability.event_publisher import (
    EventPublisherProtocol,
    InProcessEventPublisher,
)
from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.observability.sentry import init_sentry
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.request_log import router as request_log_router
from luthien_proxy.retention.archiver import S3ConversationArchiver
from luthien_proxy.retention.purger import ConversationPurger
from luthien_proxy.session import login_page_router
from luthien_proxy.session import router as session_router
from luthien_proxy.settings import Settings, clear_settings_cache, get_settings
from luthien_proxy.telemetry import (
    configure_logging,
    configure_tracing,
    instrument_app,
    instrument_redis,
)
from luthien_proxy.ui import router as ui_router
from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.usage_telemetry.config import resolve_telemetry_config
from luthien_proxy.usage_telemetry.sender import TelemetrySender
from luthien_proxy.utils import db
from luthien_proxy.utils.constants import DB_URL_PREVIEW_LENGTH
from luthien_proxy.utils.credential_cache import (
    CredentialCacheProtocol,
    InProcessCredentialCache,
    RedisCredentialCache,
)
from luthien_proxy.utils.migration_check import check_migrations
from luthien_proxy.utils.url import sanitize_url_for_logging
from luthien_proxy.version import PROXY_DISPLAY_VERSION
from luthien_proxy.webhook.sender import WebhookSender

# Configure OpenTelemetry tracing and logging EARLY (before app creation)
# This ensures the tracer provider is set up before any spans are created
configure_tracing()
configure_logging()
instrument_redis()

init_sentry()

logger = logging.getLogger(__name__)

_HTTP_STATUS_TO_ANTHROPIC_ERROR_TYPE = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    503: "overloaded_error",
    529: "overloaded_error",
}


def http_status_to_anthropic_error_type(status_code: int) -> str:
    """Map HTTP status code to Anthropic error type string."""
    return _HTTP_STATUS_TO_ANTHROPIC_ERROR_TYPE.get(status_code, "api_error")


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Format HTTPExceptions in Anthropic style for /v1/messages paths.

    Note: exc is typed as Exception to satisfy Starlette's ExceptionHandler protocol,
    but FastAPI guarantees it will be FastAPIHTTPException when registered for that type.
    """
    http_exc: FastAPIHTTPException = exc  # type: ignore[assignment]
    if request.url.path.startswith("/v1/messages"):
        error_type = http_status_to_anthropic_error_type(http_exc.status_code)
        message = http_exc.detail if isinstance(http_exc.detail, str) else str(http_exc.detail)
        content = {
            "type": "error",
            "error": {
                "type": error_type,
                "message": message,
            },
        }
        return JSONResponse(status_code=http_exc.status_code, content=content)
    return JSONResponse(
        status_code=http_exc.status_code,
        content={"detail": http_exc.detail},
        headers=dict(http_exc.headers) if http_exc.headers else None,
    )


async def request_validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Format RequestValidationErrors in Anthropic style for /v1/messages paths.

    Note: exc is typed as Exception to satisfy Starlette's ExceptionHandler protocol,
    but FastAPI guarantees it will be RequestValidationError when registered for that type.
    """
    validation_exc: RequestValidationError = exc  # type: ignore[assignment]
    if request.url.path.startswith("/v1/messages"):
        content = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": str(validation_exc),
            },
        }
        return JSONResponse(status_code=422, content=content)
    return JSONResponse(status_code=422, content={"detail": validation_exc.errors()})


def create_app(
    api_key: str | None,
    admin_key: str | None,
    db_pool: db.DatabasePool,
    redis_client: Redis | None,
    startup_policy_path: str | None = None,
    policy_source: str = "db-fallback-file",
    auth_mode: AuthMode = AuthMode.BOTH,
    cli_overrides: dict[str, object] | None = None,
) -> FastAPI:
    """Create FastAPI application with dependency injection.

    Args:
        api_key: Shared API key the gateway accepts from clients (CLIENT_API_KEY)
        admin_key: API key for admin operations (ADMIN_API_KEY)
        db_pool: Database connection pool (already initialized)
        redis_client: Redis client (None for SQLite/local mode)
        startup_policy_path: Optional path to YAML policy config to load at startup
        policy_source: Strategy for loading policy at startup (db, file, db-fallback-file, file-fallback-db)
        auth_mode: Authentication mode ("client_key", "passthrough", or "both")
        cli_overrides: CLI argument overrides for config values

    Returns:
        Configured FastAPI application with all routes and middleware
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifespan: startup and shutdown."""
        # Startup
        logger.info("Starting Luthien Gateway...")

        # Validate migrations are up to date before proceeding
        await check_migrations(db_pool)
        logger.info("Migration check passed")

        # Initialize config registry (CLI > env > DB > defaults)
        settings = get_settings()
        _config_registry = ConfigRegistry(
            settings=settings,
            db_pool=db_pool,
            cli_overrides=cli_overrides,
        )
        await _config_registry.initialize()
        logger.info("Config registry initialized")

        # Configure litellm globally (moved from policy file to prevent import side effects)
        litellm.drop_params = True
        logger.info("Configured litellm: drop_params=True")

        # Create event publisher (Redis or in-process)
        _event_publisher: EventPublisherProtocol
        if redis_client:
            _event_publisher = RedisEventPublisher(redis_client)
        else:
            _event_publisher = InProcessEventPublisher()
            logger.info("Using in-process event publisher (no Redis)")

        _emitter = EventEmitter(
            db_pool=db_pool,
            event_publisher=_event_publisher,
            stdout_enabled=True,
        )
        logger.info("Event emitter created")

        # Initialize PolicyManager
        try:
            _policy_manager = PolicyManager(
                db_pool=db_pool,
                redis_client=redis_client,
                startup_policy_path=startup_policy_path,
                policy_source=policy_source,
            )
            await _policy_manager.initialize()
            logger.info(f"PolicyManager initialized (policy: {_policy_manager.current_policy.__class__.__name__})")
        except Exception as exc:
            logger.error(f"Failed to initialize PolicyManager: {exc}", exc_info=True)
            raise RuntimeError(f"Failed to initialize PolicyManager: {exc}") from exc

        # Create Anthropic client if API key is configured.
        # Used as the server-side credential in client_key and both modes.
        _anthropic_client: AnthropicClient | None = None
        anthropic_api_key = settings.anthropic_api_key
        if anthropic_api_key:
            _anthropic_client = AnthropicClient(api_key=anthropic_api_key)

        # Create credential cache (Redis or in-process)
        _credential_cache: CredentialCacheProtocol | None
        if redis_client:
            _credential_cache = RedisCredentialCache(redis_client)
        else:
            _credential_cache = InProcessCredentialCache()
            logger.info("Using in-process credential cache (no Redis)")

        # Initialize CredentialManager for passthrough auth + server credentials
        _enc_key_str = get_settings().credential_encryption_key
        encryption_key = _enc_key_str.encode() if _enc_key_str else None
        _credential_manager = CredentialManager(db_pool=db_pool, cache=_credential_cache, encryption_key=encryption_key)
        await _credential_manager.initialize(default_auth_mode=auth_mode)

        _resolved_mode = _credential_manager.config.auth_mode.value
        if _resolved_mode == "client_key":
            logger.warning("Upstream auth mode: client_key — all requests billed to server ANTHROPIC_API_KEY.")
        elif _resolved_mode == "both":
            logger.info(
                "Upstream auth mode: both — forwards client credentials by default; falls back to server key on CLIENT_API_KEY match."
            )
        else:
            logger.info("Upstream auth mode: passthrough — client credentials forwarded directly to Anthropic.")

        if api_key is None and _resolved_mode == "client_key":
            raise RuntimeError(
                "AUTH_MODE=client_key requires CLIENT_API_KEY to be set. "
                "Either set CLIENT_API_KEY or switch to AUTH_MODE=both or AUTH_MODE=passthrough."
            )
        elif api_key is None and _resolved_mode == "both":
            logger.info("CLIENT_API_KEY is not set — shared-key auth unavailable, using passthrough only")

        # Check if request logging is enabled
        _enable_request_logging = get_settings().enable_request_logging
        if _enable_request_logging:
            logger.info("Request/response logging ENABLED")

        # Initialize usage telemetry
        settings = get_settings()
        _telemetry_config = await resolve_telemetry_config(
            db_pool=db_pool,
            env_value=settings.usage_telemetry,
        )
        _usage_collector: UsageCollector | None = None
        _telemetry_sender: TelemetrySender | None = None
        if _telemetry_config.enabled:
            _usage_collector = UsageCollector()
            _telemetry_sender = TelemetrySender(
                config=_telemetry_config,
                collector=_usage_collector,
                endpoint=settings.telemetry_endpoint,
            )
            _telemetry_sender.start()
        else:
            logger.info("Usage telemetry disabled")

        # Initialize conversation retention purger
        _purger: ConversationPurger | None = None
        _retention_days = get_settings().conversation_retention_days
        if _retention_days is not None and _retention_days > 0:
            _s3_bucket = get_settings().archive_s3_bucket
            _archiver: S3ConversationArchiver | None = None
            if _s3_bucket:
                _s3_prefix = get_settings().archive_s3_prefix
                _archiver = S3ConversationArchiver(bucket=_s3_bucket, prefix=_s3_prefix)
                logger.info(
                    "Conversation archival enabled: s3://%s/%s",
                    _s3_bucket,
                    _s3_prefix,
                )
            _purger = ConversationPurger(db_pool=db_pool, retention_days=_retention_days, archiver=_archiver)
            _purger.start()
        else:
            logger.info("Conversation retention disabled (CONVERSATION_RETENTION_DAYS not set)")

        # Initialize webhook sender
        _webhook_url = settings.webhook_url or None
        _webhook_sender = WebhookSender(
            url=_webhook_url,
            max_retries=settings.webhook_max_retries,
            retry_delay_seconds=settings.webhook_retry_delay_seconds,
            max_pending_tasks=settings.webhook_max_pending_tasks,
        )
        if _webhook_sender.enabled:
            logger.info("Webhook event export enabled (url=%s)", _webhook_sender.safe_url)
        else:
            logger.info("Webhook event export disabled (set WEBHOOK_URL to enable)")

        # Create Dependencies container with all services
        _dependencies = Dependencies(
            db_pool=db_pool,
            redis_client=redis_client,
            policy_manager=_policy_manager,
            emitter=_emitter,
            api_key=api_key,
            admin_key=admin_key,
            anthropic_client=_anthropic_client,
            event_publisher=_event_publisher,
            credential_manager=_credential_manager,
            enable_request_logging=_enable_request_logging,
            usage_collector=_usage_collector,
            config_registry=_config_registry,
            webhook_sender=_webhook_sender,
        )

        # Store dependencies container in app state
        app.state.dependencies = _dependencies
        logger.info("Dependencies container initialized")

        yield

        # Shutdown
        if _purger is not None:
            await _purger.stop()
        await _webhook_sender.stop()
        if _telemetry_sender is not None:
            await _telemetry_sender.stop()
        await _credential_manager.close()
        await anthropic_client_cache.close_all()
        await _passthrough_client.aclose()
        # Note: db_pool and redis_client are NOT closed here - they are owned by
        # the caller who passed them in. The caller is responsible for cleanup.
        logger.info("Luthien Gateway shutdown complete")

    # === APP SETUP ===
    app = FastAPI(
        title="Luthien Proxy Gateway",
        description="Multi-provider LLM proxy with integrated control plane",
        version=PROXY_DISPLAY_VERSION,
        lifespan=lifespan,
    )

    # Mount static files for activity monitor UI
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Add cache headers to static file responses.
    # JS/HTML/CSS use no-cache so the browser always revalidates (prevents
    # stale JS after a gateway restart). Other assets (images, fonts) get a
    # longer TTL since they change infrequently.
    class StaticCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/api/") or request.url.path == "/health":
                # Prevent CDN/edge caching of API and health responses (Railway, Cloudflare, etc.)
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            elif request.url.path.startswith("/static/"):
                path = request.url.path
                if path.endswith((".js", ".html", ".css")):
                    response.headers["Cache-Control"] = "no-cache"
                else:
                    response.headers["Cache-Control"] = "public, max-age=3600"
            return response

    app.add_middleware(StaticCacheMiddleware)

    # Include routers
    app.include_router(gateway_router)  # /v1/messages
    app.include_router(debug_router)  # /api/debug/*
    app.include_router(ui_router)  # /activity/*, /policy-config, /diffs
    app.include_router(admin_router)  # /api/admin/* (policy management)
    app.include_router(session_router)  # /auth/login, /auth/logout
    app.include_router(login_page_router)  # /login (convenience redirect)
    app.include_router(history_routes.router)  # /history/* (conversation history UI)
    app.include_router(history_routes.api_router)  # /api/history/* (conversation history API)
    app.include_router(request_log_router)  # /request-logs/* (HTTP-level logging)

    # Simple utility endpoints
    @app.get("/health")
    async def health(request: Request):
        """Health check endpoint (liveness probe).

        Returns gateway status including database connectivity. Always returns
        HTTP 200 — the process is alive even if the DB is unreachable. Use
        /ready for readiness probes that should fail when the DB is down.
        """
        deps = getattr(request.app.state, "dependencies", None)
        auth_mode = None
        if deps and deps.credential_manager:
            auth_mode = deps.credential_manager.config.auth_mode.value

        last_credential_type = None
        last_credential_at = None
        if deps and deps.last_credential_info:
            last_credential_type = deps.last_credential_info.get("type")
            last_credential_at = deps.last_credential_info.get("timestamp")

        # Ping database to report connectivity
        db_status = "not_configured"
        db_error = None
        if deps and deps.db_pool:
            try:
                pool = await deps.db_pool.get_pool()
                result = await pool.fetchval("SELECT 1")
                db_status = "connected" if result == 1 else "unexpected_response"
            except Exception as exc:
                db_status = "unreachable"
                db_error = str(exc)

        status = "healthy" if db_status in ("connected", "not_configured") else "degraded"

        response = {
            "status": status,
            "version": PROXY_DISPLAY_VERSION,
            "database": db_status,
            "auth_mode": auth_mode,
            "last_credential_type": last_credential_type,
            "last_credential_at": last_credential_at,
        }
        if db_error:
            response["database_error"] = db_error
        return response

    @app.get("/ready")
    async def ready(request: Request):
        """Readiness probe endpoint.

        Returns HTTP 200 only when the gateway is fully ready to serve traffic:
        database connected and policy loaded. Returns HTTP 503 otherwise.

        Use this for load balancer readiness probes (ECS, Kubernetes) so traffic
        is not routed until the gateway can actually handle requests. Use /health
        for liveness probes.
        """
        deps = getattr(request.app.state, "dependencies", None)
        reasons: list[str] = []

        # Check dependencies container exists
        if not deps:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reasons": ["dependencies not initialized"]},
            )

        # Check database connectivity
        if deps.db_pool:
            try:
                pool = await deps.db_pool.get_pool()
                await pool.fetchval("SELECT 1")
            except Exception as exc:
                reasons.append(f"database unreachable: {exc}")
        # No db_pool is acceptable (SQLite auto-creates, or DB is optional)

        # Check policy is loaded
        try:
            current_policy = deps.policy_manager.current_policy
            if current_policy is None:
                reasons.append("no policy loaded")
        except Exception as exc:
            reasons.append(f"policy unavailable: {exc}")

        if reasons:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reasons": reasons},
            )

        return {"status": "ready"}

    # Format HTTPExceptions and validation errors as Anthropic errors on /v1/messages paths
    app.add_exception_handler(FastAPIHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)

    # Exception handler for backend API errors
    @app.exception_handler(BackendAPIError)
    async def backend_api_error_handler(request: Request, exc: BackendAPIError) -> JSONResponse:
        """Handle errors from backend LLM providers.

        Formats error responses in Anthropic format.
        Also invalidates cached credentials on 401 so stale "valid" entries
        don't let rejected keys keep passing auth.
        """
        if exc.status_code == 401 and hasattr(request.state, "passthrough_credential"):
            deps = getattr(request.app.state, "dependencies", None)
            cm = getattr(deps, "credential_manager", None) if deps else None
            if cm is not None:
                await cm.on_backend_401(request.state.passthrough_credential)

        content = {
            "type": "error",
            "error": {
                "type": exc.error_type,
                "message": exc.message,
            },
        }
        return JSONResponse(status_code=exc.status_code, content=content)

    # Instrument FastAPI AFTER routes are registered
    # This ensures all endpoints get traced
    instrument_app(app)

    return app


async def connect_db(database_url: str) -> db.DatabasePool:
    """Create and initialize database connection pool.

    Args:
        database_url: PostgreSQL connection URL

    Returns:
        Initialized DatabasePool

    Raises:
        RuntimeError: If connection fails
    """
    try:
        pool = db.DatabasePool(database_url)
        await pool.get_pool()
        logger.info(f"Connected to database at {database_url[:DB_URL_PREVIEW_LENGTH]}...")
        return pool
    except Exception as exc:
        raise RuntimeError(f"Failed to connect to database: {exc}") from exc


async def connect_redis(redis_url: str) -> Redis:
    """Create and initialize Redis client.

    Args:
        redis_url: Redis connection URL

    Returns:
        Connected Redis client

    Raises:
        RuntimeError: If connection fails
    """
    try:
        client: Redis = Redis.from_url(redis_url, decode_responses=False)
        await client.ping()
        logger.info(f"Connected to Redis at {sanitize_url_for_logging(redis_url)}")
        return client
    except Exception as exc:
        raise RuntimeError(f"Failed to connect to Redis: {exc}") from exc


def load_config_from_env(settings: Settings | None = None) -> dict:
    """Load and validate configuration from environment variables.

    Collects missing-required-field errors and reports them at once, so
    operators can fix every missing variable in a single deploy cycle.
    Pydantic structural errors (type mismatches on settings fields) are
    still raised immediately.

    Args:
        settings: Optional Settings instance for testing. Uses get_settings() if None.

    Returns:
        Dictionary with configuration values (api_key, admin_key, database_url,
        redis_url, startup_policy_path)

    Raises:
        ValueError: If required environment variables are missing or invalid.
    """
    errors: list[str] = []

    try:
        if settings is None:
            settings = get_settings()
    except ValidationError as e:
        raise ValueError(f"Invalid configuration: {e}")

    # Both api keys are optional — passthrough mode doesn't need CLIENT_API_KEY,
    # and admin endpoints degrade gracefully without ADMIN_API_KEY.

    database_url = settings.database_url
    if not database_url:
        errors.append("DATABASE_URL environment variable required")

    if errors:
        bullet = "\n  - "
        raise ValueError(f"Missing required configuration:{bullet}{bullet.join(errors)}")

    redis_url = settings.redis_url

    return {
        "api_key": settings.client_api_key,
        "admin_key": settings.admin_api_key,
        "database_url": database_url,
        "redis_url": redis_url,
        "startup_policy_path": settings.policy_config if settings.policy_config else None,
        "policy_source": settings.policy_source,
        "gateway_port": settings.gateway_port,
        "auth_mode": settings.auth_mode,
    }


def configure_local_mode() -> None:
    """Force-set env vars for dockerless local mode.

    Infrastructure vars (DATABASE_URL, REDIS_URL, etc.) are force-set because
    litellm calls dotenv.load_dotenv() at import time, polluting os.environ
    with Docker-internal values from .env.
    """
    data_dir = os.path.join(os.path.expanduser("~"), ".luthien")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "local.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["REDIS_URL"] = ""
    os.environ["POLICY_CONFIG"] = "config/policy_config.yaml"
    os.environ["POLICY_SOURCE"] = "file"


def _is_railway() -> bool:
    """Detect if running on Railway (RAILWAY_SERVICE_NAME is auto-injected)."""
    return bool(os.environ.get("RAILWAY_SERVICE_NAME"))


def propagate_cli_overrides_to_env(
    cli_overrides: dict[str, object],
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Write coerced CLI override values into an environment mapping.

    Startup-critical reads (uvicorn port binding, connect_db, policy loading,
    auth mode) all happen BEFORE the ConfigRegistry is constructed inside the
    lifespan. Without this propagation, CLI flags for those fields are silently
    ignored at boot. By injecting into os.environ, load_config_from_env() sees
    the CLI values via get_settings() on the startup path.

    Args:
        cli_overrides: {field_name: coerced_value} from argparse.
        environ: Mapping to write into. Defaults to os.environ; tests pass an
            isolated dict to avoid polluting process state.
    """
    target = environ if environ is not None else os.environ
    for name, value in cli_overrides.items():
        meta = CONFIG_FIELDS_BY_NAME[name]
        if isinstance(value, bool):
            env_val = "true" if value else "false"
        elif isinstance(value, enum.Enum):
            env_val = str(value.value)
        else:
            env_val = str(value)
        target[meta.env_var] = env_val


def auto_provision_defaults() -> dict[str, str]:
    """Auto-provision sensible defaults for missing environment variables.

    Ensures the app can boot on fresh PaaS deployments (Railway, Render, etc.)
    without any pre-configured environment variables. Only sets values that are
    not already present — never overrides explicit configuration.

    On Railway, additional defaults are applied:
      - AUTH_MODE=passthrough (OAuth pass-through, no server API key needed)
      - POLICY_CONFIG=config/railway_policy_config.yaml (logging + English rules)
      - LOCALHOST_AUTH_BYPASS=false (not applicable on cloud)

    Returns:
        Dict of variable names to auto-provisioned values (empty if nothing was provisioned).
    """
    provisioned: dict[str, str] = {}
    on_railway = _is_railway()

    if not os.environ.get("DATABASE_URL"):
        data_dir = os.path.join(os.path.expanduser("~"), ".luthien")
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "local.db")
        value = f"sqlite:///{db_path}"
        os.environ["DATABASE_URL"] = value
        provisioned["DATABASE_URL"] = value

    if not os.environ.get("ADMIN_API_KEY"):
        value = f"admin-{secrets.token_urlsafe(16)}"
        os.environ["ADMIN_API_KEY"] = value
        provisioned["ADMIN_API_KEY"] = value

    if not os.environ.get("POLICY_CONFIG"):
        value = "config/railway_policy_config.yaml" if on_railway else "config/policy_config.yaml"
        os.environ["POLICY_CONFIG"] = value
        provisioned["POLICY_CONFIG"] = value

    if not os.environ.get("POLICY_SOURCE"):
        value = "file"
        os.environ["POLICY_SOURCE"] = value
        provisioned["POLICY_SOURCE"] = value

    # Railway-specific defaults: passthrough auth and no localhost bypass
    if on_railway:
        if not os.environ.get("AUTH_MODE"):
            value = "passthrough"
            os.environ["AUTH_MODE"] = value
            provisioned["AUTH_MODE"] = value

        if not os.environ.get("LOCALHOST_AUTH_BYPASS"):
            os.environ["LOCALHOST_AUTH_BYPASS"] = "false"
            provisioned["LOCALHOST_AUTH_BYPASS"] = "false"

    return provisioned


__all__ = [
    "create_app",
    "load_config_from_env",
    "connect_db",
    "connect_redis",
    "configure_local_mode",
    "auto_provision_defaults",
]


if __name__ == "__main__":
    import asyncio

    async def main():
        """Production entry point with proper resource lifecycle."""
        parser = argparse.ArgumentParser(description="Luthien Gateway")
        parser.add_argument(
            "--local",
            action="store_true",
            help="Run with SQLite (no Redis required), no Docker needed",
        )
        # Auto-generate CLI flags from config field definitions
        for field_meta in CONFIG_FIELDS:
            flag = f"--{field_meta.name.replace('_', '-')}"
            if field_meta.field_type is bool:
                parser.add_argument(flag, type=str, default=None, metavar="BOOL", help=field_meta.description)
            elif field_meta.field_type is int:
                parser.add_argument(flag, type=int, default=None, help=field_meta.description)
            elif field_meta.field_type is float:
                parser.add_argument(flag, type=float, default=None, help=field_meta.description)
            else:
                parser.add_argument(flag, type=str, default=None, help=field_meta.description)
        args = parser.parse_args()

        # Collect CLI overrides (only non-None values) through coerce_value so
        # invalid input (e.g. --dogfood-mode ture, --auth-mode bogus) fails loudly
        # with a clear error instead of silently landing as False / raw string.
        cli_overrides: dict[str, object] = {}
        for field_meta in CONFIG_FIELDS:
            val = getattr(args, field_meta.name, None)
            if val is None:
                continue
            try:
                cli_overrides[field_meta.name] = coerce_value(field_meta, val)
            except (ValueError, TypeError) as exc:
                parser.error(f"Invalid value for --{field_meta.name.replace('_', '-')}: {exc}")

        # Propagate CLI overrides into os.environ so startup-critical reads (port
        # binding, DB pool, policy loading — all executed BEFORE ConfigRegistry is
        # constructed inside the lifespan) actually honor the flags.
        if cli_overrides:
            propagate_cli_overrides_to_env(cli_overrides)
            clear_settings_cache()

        if args.local:
            configure_local_mode()
            clear_settings_cache()
            print(f"[local mode] DATABASE_URL={os.environ['DATABASE_URL']}")

        # Auto-provision missing env vars so fresh deploys boot without config
        provisioned = auto_provision_defaults()
        if provisioned:
            clear_settings_cache()
            print("=" * 60)
            print("AUTO-CONFIGURED: Missing environment variables were set")
            print("to defaults. Set these in your platform dashboard for")
            print("production use:")
            for key, value in provisioned.items():
                print(f"  {key}={value}")
            print("=" * 60)

        config = load_config_from_env()

        startup_path = config.get("startup_policy_path")
        port = config["gateway_port"]
        logger.info(f"Policy configuration: startup_policy_path={startup_path or '(load from DB)'}")
        logger.info(f"Starting gateway on port {port}")

        db_pool = None
        redis_client = None
        try:
            db_pool = await connect_db(config["database_url"])
            redis_url = config["redis_url"]
            if redis_url:
                redis_client = await connect_redis(redis_url)
            else:
                logger.info("Redis disabled (no REDIS_URL) — running without real-time UI events")

            app = create_app(
                api_key=config["api_key"],
                admin_key=config["admin_key"],
                db_pool=db_pool,
                redis_client=redis_client,
                startup_policy_path=startup_path,
                policy_source=config["policy_source"],
                auth_mode=config.get("auth_mode", AuthMode.BOTH),
                cli_overrides=cli_overrides if cli_overrides else None,
            )

            _valid_log_levels = {"critical", "error", "warning", "info", "debug", "trace"}
            log_level = get_settings().log_level.lower()
            if log_level not in _valid_log_levels:
                logger.warning(
                    f"Invalid LOG_LEVEL '{log_level}', falling back to 'info'. "
                    f"Valid levels: {', '.join(sorted(_valid_log_levels))}"
                )
                log_level = "info"
            logger.info(f"Using log level: {log_level}")
            server_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level=log_level)
            server = uvicorn.Server(server_config)
            await server.serve()
        finally:
            if db_pool:
                await db_pool.close()
                logger.info("Closed database connection")
            if redis_client:
                await redis_client.close()
                logger.info("Closed Redis connection")

    asyncio.run(main())
