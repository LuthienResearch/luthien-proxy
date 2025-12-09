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
from luthien_proxy.utils.constants import DEFAULT_GATEWAY_PORT

# Configure OpenTelemetry tracing and logging EARLY (before app creation)
# This ensures the tracer provider is set up before any spans are created
configure_tracing()
configure_logging()
instrument_redis()

logger = logging.getLogger(__name__)


def create_app(
    api_key: str,
    admin_key: str | None,
    database_url: str,
    redis_url: str,
    policy_source: str,
    policy_config_path: str,
) -> FastAPI:
    """Create FastAPI application with dependency injection.

    Args:
        api_key: API key for client authentication (PROXY_API_KEY)
        admin_key: API key for admin operations (ADMIN_API_KEY)
        database_url: PostgreSQL database URL
        redis_url: Redis URL for event publishing
        policy_source: Policy source precedence mode
        policy_config_path: Path to YAML policy configuration

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

        # Connect to database
        _db_pool: db.DatabasePool | None = None
        try:
            _db_pool = db.DatabasePool(database_url)
            await _db_pool.get_pool()
            logger.info(f"Connected to database at {database_url[:20]}...")
        except Exception as exc:
            logger.warning(f"Failed to connect to database: {exc}. Event persistence will be disabled.")
            _db_pool = None

        # Connect to Redis
        _redis_client: Redis | None = None
        try:
            _redis_client = Redis.from_url(redis_url, decode_responses=False)
            await _redis_client.ping()
            logger.info(f"Connected to Redis at {redis_url}")
        except Exception as exc:
            logger.warning(f"Failed to connect to Redis: {exc}. Event publisher will be disabled.")
            _redis_client = None

        # Create event emitter (will be injected via Dependencies)
        _redis_publisher = RedisEventPublisher(_redis_client) if _redis_client else None
        _emitter = EventEmitter(
            db_pool=_db_pool,
            redis_publisher=_redis_publisher,
            stdout_enabled=True,
        )
        logger.info("Event emitter created")

        # Initialize PolicyManager with configured source precedence
        _policy_manager: PolicyManager | None = None
        if _db_pool and _redis_client:
            try:
                _policy_manager = PolicyManager(
                    db_pool=_db_pool,
                    redis_client=_redis_client,
                    yaml_path=policy_config_path,
                    policy_source=policy_source,  # type: ignore
                )
                await _policy_manager.initialize()
                logger.info(
                    f"PolicyManager initialized (source: {policy_source}, "
                    f"policy: {_policy_manager.current_policy.__class__.__name__})"
                )
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
            db_pool=_db_pool,
            redis_client=_redis_client,
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
        if _db_pool:
            await _db_pool.close()
            logger.info("Closed database connection")
        if _redis_client:
            await _redis_client.close()
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


def load_config_from_env(settings: Settings | None = None) -> dict:
    """Load and validate configuration from environment variables.

    Args:
        settings: Optional Settings instance for testing. Uses get_settings() if None.

    Returns:
        Dictionary with configuration values ready for create_app()

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
        "policy_source": settings.policy_source,
        "policy_config_path": settings.policy_config,
    }


__all__ = ["create_app", "load_config_from_env"]


if __name__ == "__main__":
    config = load_config_from_env()
    logger.info(f"Policy configuration: source={config['policy_source']}, path={config['policy_config_path']}")

    app = create_app(**config)
    uvicorn.run(app, host="0.0.0.0", port=DEFAULT_GATEWAY_PORT, log_level="debug")
