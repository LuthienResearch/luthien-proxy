"""Luthien - integrated FastAPI + LiteLLM proxy with OpenTelemetry observability."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
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
from luthien_proxy.credential_manager import AuthMode, CredentialManager
from luthien_proxy.debug import router as debug_router
from luthien_proxy.dependencies import Dependencies
from luthien_proxy.exceptions import BackendAPIError
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
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.request_log import router as request_log_router
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

# Configure OpenTelemetry tracing and logging EARLY (before app creation)
# This ensures the tracer provider is set up before any spans are created
configure_tracing()
configure_logging()
instrument_redis()

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
    api_key: str,
    admin_key: str | None,
    db_pool: db.DatabasePool,
    redis_client: Redis | None,
    startup_policy_path: str | None = None,
    policy_source: str = "db-fallback-file",
    auth_mode: AuthMode = AuthMode.BOTH,
) -> FastAPI:
    """Create FastAPI application with dependency injection.

    Args:
        api_key: API key for client authentication (PROXY_API_KEY)
        admin_key: API key for admin operations (ADMIN_API_KEY)
        db_pool: Database connection pool (already initialized)
        redis_client: Redis client (None for SQLite/local mode)
        startup_policy_path: Optional path to YAML policy config to load at startup
        policy_source: Strategy for loading policy at startup (db, file, db-fallback-file, file-fallback-db)
        auth_mode: Authentication mode ("proxy_key", "passthrough", or "both")

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
        # Used as the server-side credential in proxy_key and both modes.
        _anthropic_client: AnthropicClient | None = None
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_api_key:
            _anthropic_client = AnthropicClient(api_key=anthropic_api_key)

        # Create credential cache (Redis or in-process)
        _credential_cache: CredentialCacheProtocol | None
        if redis_client:
            _credential_cache = RedisCredentialCache(redis_client)
        else:
            _credential_cache = InProcessCredentialCache()
            logger.info("Using in-process credential cache (no Redis)")

        # Initialize CredentialManager for passthrough auth
        _credential_manager = CredentialManager(db_pool=db_pool, cache=_credential_cache)
        await _credential_manager.initialize(default_auth_mode=auth_mode)

        _resolved_mode = _credential_manager.config.auth_mode.value
        if _resolved_mode == "proxy_key":
            logger.warning("Upstream auth mode: proxy_key — all requests billed to server ANTHROPIC_API_KEY.")
        elif _resolved_mode == "both":
            logger.info("Upstream auth mode: both — uses client credentials when valid, falls back to server API key.")
        else:
            logger.info("Upstream auth mode: passthrough — client credentials forwarded directly to Anthropic.")

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
        )

        # Store dependencies container in app state
        app.state.dependencies = _dependencies
        logger.info("Dependencies container initialized")

        yield

        # Shutdown
        if _telemetry_sender is not None:
            await _telemetry_sender.stop()
        await _credential_manager.close()
        await anthropic_client_cache.close_all()
        # Note: db_pool and redis_client are NOT closed here - they are owned by
        # the caller who passed them in. The caller is responsible for cleanup.
        logger.info("Luthien Gateway shutdown complete")

    # === APP SETUP ===
    app = FastAPI(
        title="Luthien Proxy Gateway",
        description="Multi-provider LLM proxy with integrated control plane",
        version="2.0.0",
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
        """Health check endpoint.

        Returns gateway status, auth mode, and last observed credential type so
        operators and the UI can surface billing mode warnings accurately.
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

        return {
            "status": "healthy",
            "version": "2.0.0",
            "auth_mode": auth_mode,
            "last_credential_type": last_credential_type,
            "last_credential_at": last_credential_at,
        }

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
        logger.info(f"Connected to Redis at {redis_url}")
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

    if settings.proxy_api_key is None:
        errors.append("PROXY_API_KEY environment variable required")

    # admin_api_key is optional — admin endpoints return 500 if not set,
    # but the gateway still serves proxy traffic without it.

    database_url = settings.database_url
    if not database_url:
        errors.append("DATABASE_URL environment variable required")

    if errors:
        bullet = "\n  - "
        raise ValueError(f"Missing required configuration:{bullet}{bullet.join(errors)}")

    redis_url = settings.redis_url

    return {
        "api_key": settings.proxy_api_key,
        "admin_key": settings.admin_api_key,
        "database_url": database_url,
        "redis_url": redis_url,
        "startup_policy_path": settings.policy_config if settings.policy_config else None,
        "policy_source": settings.policy_source,
        "gateway_port": settings.gateway_port,
        "auth_mode": settings.auth_mode,
    }


def configure_local_mode() -> dict[str, str]:
    """Force-set env vars for dockerless local mode.

    Infrastructure vars (DATABASE_URL, REDIS_URL, etc.) are force-set because
    litellm calls dotenv.load_dotenv() at import time, polluting os.environ
    with Docker-internal values from .env. API keys use setdefault so users
    can pre-set them intentionally.

    Returns:
        Dict with proxy_api_key (whether generated or existing).
    """
    data_dir = os.path.join(os.path.expanduser("~"), ".luthien")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "local.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["REDIS_URL"] = ""
    os.environ["POLICY_CONFIG"] = "config/policy_config.yaml"
    os.environ["POLICY_SOURCE"] = "file"

    if not os.environ.get("PROXY_API_KEY"):
        key = f"sk-local-{secrets.token_urlsafe(16)}"
        os.environ["PROXY_API_KEY"] = key

    return {
        "proxy_api_key": os.environ["PROXY_API_KEY"],
    }


def auto_provision_defaults() -> dict[str, str]:
    """Auto-provision sensible defaults for missing environment variables.

    Ensures the app can boot on fresh PaaS deployments (Railway, Render, etc.)
    without any pre-configured environment variables. Only sets values that are
    not already present — never overrides explicit configuration.

    Returns:
        Dict of variable names to auto-provisioned values (empty if nothing was provisioned).
    """
    provisioned: dict[str, str] = {}

    if not os.environ.get("DATABASE_URL"):
        data_dir = os.path.join(os.path.expanduser("~"), ".luthien")
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "local.db")
        value = f"sqlite:///{db_path}"
        os.environ["DATABASE_URL"] = value
        provisioned["DATABASE_URL"] = value

    if not os.environ.get("PROXY_API_KEY"):
        value = f"sk-luthien-{secrets.token_urlsafe(16)}"
        os.environ["PROXY_API_KEY"] = value
        provisioned["PROXY_API_KEY"] = value

    if not os.environ.get("ADMIN_API_KEY"):
        value = f"admin-{secrets.token_urlsafe(16)}"
        os.environ["ADMIN_API_KEY"] = value
        provisioned["ADMIN_API_KEY"] = value

    if not os.environ.get("POLICY_CONFIG"):
        value = "config/policy_config.yaml"
        os.environ["POLICY_CONFIG"] = value
        provisioned["POLICY_CONFIG"] = value

    if not os.environ.get("POLICY_SOURCE"):
        value = "file"
        os.environ["POLICY_SOURCE"] = value
        provisioned["POLICY_SOURCE"] = value

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
        args = parser.parse_args()

        if args.local:
            keys = configure_local_mode()
            clear_settings_cache()
            print(f"[local mode] DATABASE_URL={os.environ['DATABASE_URL']}")
            print(f"[local mode] PROXY_API_KEY={keys['proxy_api_key']}")

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
            )

            server_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="debug")
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
