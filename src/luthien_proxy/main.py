"""Luthien - integrated FastAPI + LiteLLM proxy with OpenTelemetry observability."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import litellm
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware

from luthien_proxy.admin import router as admin_router
from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.debug import router as debug_router
from luthien_proxy.dependencies import Dependencies
from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.gateway_routes import router as gateway_router
from luthien_proxy.history import routes as history_routes
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.litellm_client import LiteLLMClient
from luthien_proxy.observability.emitter import EventEmitter
from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.pipeline.client_format import ClientFormat
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.session import login_page_router
from luthien_proxy.session import router as session_router
from luthien_proxy.settings import Settings, get_settings
from luthien_proxy.telemetry import (
    configure_logging,
    configure_tracing,
    instrument_app,
    instrument_redis,
)
from luthien_proxy.ui import router as ui_router
from luthien_proxy.utils import db
from luthien_proxy.utils.constants import DB_URL_PREVIEW_LENGTH
from luthien_proxy.utils.migration_check import check_migrations

# Configure OpenTelemetry tracing and logging EARLY (before app creation)
# This ensures the tracer provider is set up before any spans are created
configure_tracing()
configure_logging()
instrument_redis()

logger = logging.getLogger(__name__)


def create_app(
    api_key: str,
    admin_key: str | None,
    db_pool: db.DatabasePool,
    redis_client: Redis,
    startup_policy_path: str | None = None,
    policy_source: str = "db-fallback-file",
    auth_mode: str = "both",
) -> FastAPI:
    """Create FastAPI application with dependency injection.

    Args:
        api_key: API key for client authentication (PROXY_API_KEY)
        admin_key: API key for admin operations (ADMIN_API_KEY)
        db_pool: Database connection pool (already initialized)
        redis_client: Redis client (already initialized)
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

        # Create event emitter (will be injected via Dependencies)
        _redis_publisher = RedisEventPublisher(redis_client)
        _emitter = EventEmitter(
            db_pool=db_pool,
            redis_publisher=_redis_publisher,
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

        # Create LLM client (singleton for the app lifetime)
        _llm_client = LiteLLMClient()
        logger.info("LLM client initialized")

        # Create Anthropic client if API key is configured
        _anthropic_client: AnthropicClient | None = None
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_api_key:
            _anthropic_client = AnthropicClient(api_key=anthropic_api_key)
            logger.info("Anthropic client initialized")
        else:
            logger.info("ANTHROPIC_API_KEY not set - native Anthropic path disabled")

        # Initialize CredentialManager for passthrough auth
        _credential_manager = CredentialManager(db_pool=db_pool, redis_client=redis_client)
        await _credential_manager.initialize(default_auth_mode=auth_mode)
        logger.info(f"CredentialManager initialized: mode={_credential_manager.config.auth_mode.value}")

        # Create Dependencies container with all services
        _dependencies = Dependencies(
            db_pool=db_pool,
            redis_client=redis_client,
            llm_client=_llm_client,
            policy_manager=_policy_manager,
            emitter=_emitter,
            api_key=api_key,
            admin_key=admin_key,
            anthropic_client=_anthropic_client,
            credential_manager=_credential_manager,
        )

        # Store dependencies container in app state
        app.state.dependencies = _dependencies
        logger.info("Dependencies container initialized")

        yield

        # Shutdown
        await _credential_manager.close()
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

    # Add cache headers to static file responses
    class StaticCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "public, max-age=3600"
            return response

    app.add_middleware(StaticCacheMiddleware)

    # Include routers
    app.include_router(gateway_router)  # /v1/chat/completions, /v1/messages (PolicyOrchestrator)
    app.include_router(debug_router)  # /debug/*
    app.include_router(ui_router)  # /activity/*, /policy-config
    app.include_router(admin_router)  # /admin/* (policy management)
    app.include_router(session_router)  # /auth/login, /auth/logout
    app.include_router(login_page_router)  # /login (convenience redirect)
    app.include_router(history_routes.router)  # /history/* (conversation history viewer)

    # Simple utility endpoints
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "version": "2.0.0"}

    # Exception handler for backend API errors
    @app.exception_handler(BackendAPIError)
    async def backend_api_error_handler(request: Request, exc: BackendAPIError) -> JSONResponse:
        """Handle errors from backend LLM providers.

        Formats the error response according to the client's API format
        (Anthropic or OpenAI) so clients receive properly structured errors.
        Also invalidates cached credentials on 401 so stale "valid" entries
        don't let rejected keys keep passing auth.
        """
        if exc.status_code == 401 and hasattr(request.state, "passthrough_api_key"):
            deps = getattr(request.app.state, "dependencies", None)
            cm = getattr(deps, "credential_manager", None) if deps else None
            if cm is not None:
                await cm.on_backend_401(request.state.passthrough_api_key)

        if exc.client_format == ClientFormat.ANTHROPIC:
            content = {
                "type": "error",
                "error": {
                    "type": exc.error_type,
                    "message": exc.message,
                },
            }
        else:
            # OpenAI format
            content = {
                "error": {
                    "message": exc.message,
                    "type": exc.error_type,
                    "param": None,
                    "code": None,
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

    Args:
        settings: Optional Settings instance for testing. Uses get_settings() if None.

    Returns:
        Dictionary with configuration values (api_key, admin_key, database_url,
        redis_url, startup_policy_path)

    Raises:
        ValueError: If required environment variables are missing or invalid
    """
    try:
        if settings is None:
            settings = get_settings()
    except ValidationError as e:
        raise ValueError(f"Invalid configuration: {e}")

    if settings.proxy_api_key is None:
        raise ValueError("PROXY_API_KEY environment variable required")

    if settings.admin_api_key is None:
        raise ValueError("ADMIN_API_KEY environment variable required")

    if not settings.database_url:
        raise ValueError("DATABASE_URL environment variable required")

    return {
        "api_key": settings.proxy_api_key,
        "admin_key": settings.admin_api_key,
        "database_url": settings.database_url,
        "redis_url": settings.redis_url,
        "startup_policy_path": settings.policy_config if settings.policy_config else None,
        "policy_source": settings.policy_source,
        "gateway_port": settings.gateway_port,
        "auth_mode": settings.auth_mode,
    }


__all__ = ["create_app", "load_config_from_env", "connect_db", "connect_redis"]


if __name__ == "__main__":
    import asyncio

    async def main():
        """Production entry point with proper resource lifecycle."""
        config = load_config_from_env()

        startup_path = config.get("startup_policy_path")
        port = config["gateway_port"]
        logger.info(f"Policy configuration: startup_policy_path={startup_path or '(load from DB)'}")
        logger.info(f"Starting gateway on port {port}")

        db_pool = None
        redis_client = None
        try:
            db_pool = await connect_db(config["database_url"])
            redis_client = await connect_redis(config["redis_url"])

            app = create_app(
                api_key=config["api_key"],
                admin_key=config["admin_key"],
                db_pool=db_pool,
                redis_client=redis_client,
                startup_policy_path=startup_path,
                policy_source=config["policy_source"],
                auth_mode=config.get("auth_mode", "both"),
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
