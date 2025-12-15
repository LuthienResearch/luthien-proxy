"""Luthien - integrated FastAPI + LiteLLM proxy with OpenTelemetry observability."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from redis.asyncio import Redis

from luthien_proxy.admin import router as admin_router
from luthien_proxy.debug import router as debug_router
from luthien_proxy.dependencies import Dependencies
from luthien_proxy.gateway_routes import router as gateway_router
from luthien_proxy.llm.litellm_client import LiteLLMClient
from luthien_proxy.observability.emitter import EventEmitter
from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
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
from luthien_proxy.utils.constants import DB_URL_PREVIEW_LENGTH, DEFAULT_GATEWAY_PORT

# Configure OpenTelemetry tracing and logging EARLY (before app creation)
# This ensures the tracer provider is set up before any spans are created
configure_tracing()
configure_logging()
instrument_redis()

logger = logging.getLogger(__name__)


def create_app(
    api_key: str,
    admin_key: str | None,
    db_pool: db.DatabasePool | None,
    redis_client: Redis | None,
    startup_policy_path: str | None = None,
) -> FastAPI:
    """Create FastAPI application with dependency injection.

    Args:
        api_key: API key for client authentication (PROXY_API_KEY)
        admin_key: API key for admin operations (ADMIN_API_KEY)
        db_pool: Database connection pool (already initialized)
        redis_client: Redis client (already initialized)
        startup_policy_path: Optional path to YAML policy config to load at startup
                             (overrides DB, persists to DB). If None, loads from DB only.

    Returns:
        Configured FastAPI application with all routes and middleware
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifespan: startup and shutdown."""
        # Startup
        logger.info("Starting Luthien Gateway...")

        # Configure litellm globally (moved from policy file to prevent import side effects)
        litellm.drop_params = True
        logger.info("Configured litellm: drop_params=True")

        # Log connection status (objects are already initialized by caller)
        if db_pool:
            logger.info("Database pool provided")
        else:
            logger.warning("No database pool provided. Event persistence will be disabled.")

        if redis_client:
            logger.info("Redis client provided")
        else:
            logger.warning("No Redis client provided. Event publisher will be disabled.")

        # Create event emitter (will be injected via Dependencies)
        _redis_publisher = RedisEventPublisher(redis_client) if redis_client else None
        _emitter = EventEmitter(
            db_pool=db_pool,
            redis_publisher=_redis_publisher,
            stdout_enabled=True,
        )
        logger.info("Event emitter created")

        # Initialize PolicyManager
        _policy_manager: PolicyManager | None = None
        if db_pool and redis_client:
            try:
                _policy_manager = PolicyManager(
                    db_pool=db_pool,
                    redis_client=redis_client,
                    startup_policy_path=startup_policy_path,
                )
                await _policy_manager.initialize()
                logger.info(f"PolicyManager initialized (policy: {_policy_manager.current_policy.__class__.__name__})")
            except Exception as exc:
                logger.error(f"Failed to initialize PolicyManager: {exc}", exc_info=True)
                raise RuntimeError(f"Failed to initialize PolicyManager: {exc}")
        else:
            logger.error("Cannot initialize PolicyManager without database and Redis")
            raise RuntimeError("Database and Redis required for PolicyManager")

        # Create LLM client (singleton for the app lifetime)
        _llm_client = LiteLLMClient()
        logger.info("LLM client initialized")

        # Create Dependencies container with all services
        _dependencies = Dependencies(
            db_pool=db_pool,
            redis_client=redis_client,
            llm_client=_llm_client,
            policy_manager=_policy_manager,
            emitter=_emitter,
            api_key=api_key,
            admin_key=admin_key,
        )

        # Store dependencies container in app state
        app.state.dependencies = _dependencies
        logger.info("Dependencies container initialized")

        yield

        # Shutdown
        if db_pool:
            await db_pool.close()
            logger.info("Closed database connection")
        if redis_client:
            await redis_client.close()
            logger.info("Closed Redis connection")

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

    # Include routers
    app.include_router(gateway_router)  # /v1/chat/completions, /v1/messages (PolicyOrchestrator)
    app.include_router(debug_router)  # /debug/*
    app.include_router(ui_router)  # /activity/*, /policy-config
    app.include_router(admin_router)  # /admin/* (policy management)
    app.include_router(session_router)  # /auth/login, /auth/logout
    app.include_router(login_page_router)  # /login (convenience redirect)

    # Simple utility endpoints
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "version": "2.0.0"}

    # Instrument FastAPI AFTER routes are registered
    # This ensures all endpoints get traced
    instrument_app(app)

    return app


async def connect_db(database_url: str) -> db.DatabasePool | None:
    """Create and initialize database connection pool.

    Args:
        database_url: PostgreSQL connection URL

    Returns:
        Initialized DatabasePool, or None if connection fails
    """
    try:
        pool = db.DatabasePool(database_url)
        await pool.get_pool()
        logger.info(f"Connected to database at {database_url[:DB_URL_PREVIEW_LENGTH]}...")
        return pool
    except Exception as exc:
        logger.warning(f"Failed to connect to database: {exc}. Event persistence will be disabled.")
        return None


async def connect_redis(redis_url: str) -> Redis | None:
    """Create and initialize Redis client.

    Args:
        redis_url: Redis connection URL

    Returns:
        Connected Redis client, or None if connection fails
    """
    try:
        client = Redis.from_url(redis_url, decode_responses=False)
        await client.ping()
        logger.info(f"Connected to Redis at {redis_url}")
        return client
    except Exception as exc:
        logger.warning(f"Failed to connect to Redis: {exc}. Event publisher will be disabled.")
        return None


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
    }


async def get_app(settings: Settings | None = None) -> FastAPI:
    """Create fully configured app from environment variables.

    This is the main entry point for production use. It reads configuration
    from environment variables, establishes database and Redis connections,
    and creates the FastAPI application.

    Args:
        settings: Optional Settings instance for testing. Uses get_settings() if None.

    Returns:
        Configured FastAPI application ready to serve requests
    """
    config = load_config_from_env(settings)

    startup_path = config.get("startup_policy_path")
    logger.info(f"Policy configuration: startup_policy_path={startup_path or '(load from DB)'}")

    db_pool = await connect_db(config["database_url"])
    redis_client = await connect_redis(config["redis_url"])

    return create_app(
        api_key=config["api_key"],
        admin_key=config["admin_key"],
        db_pool=db_pool,
        redis_client=redis_client,
        startup_policy_path=startup_path,
    )


__all__ = ["create_app", "get_app", "load_config_from_env", "connect_db", "connect_redis"]


if __name__ == "__main__":
    import asyncio

    async def main():
        app = await get_app()
        config = uvicorn.Config(app, host="0.0.0.0", port=DEFAULT_GATEWAY_PORT, log_level="debug")
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(main())
