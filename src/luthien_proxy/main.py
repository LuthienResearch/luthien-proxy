# ABOUTME: Main FastAPI application for integrated architecture
# ABOUTME: Factory function for creating gateway app with dependency injection

"""Luthien - integrated FastAPI + LiteLLM proxy with OpenTelemetry observability."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from luthien_proxy.admin import router as admin_router
from luthien_proxy.debug import router as debug_router
from luthien_proxy.dependencies import Dependencies
from luthien_proxy.gateway_routes import router as gateway_router
from luthien_proxy.llm.litellm_client import LiteLLMClient
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.telemetry import setup_telemetry
from luthien_proxy.ui import router as ui_router
from luthien_proxy.utils import db

# Note: RedisEventPublisher is created inside Dependencies container

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

        # Initialize OpenTelemetry
        setup_telemetry(app)
        logger.info("OpenTelemetry initialized")

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

    # Simple utility endpoints
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "version": "2.0.0"}

    return app


__all__ = ["create_app"]


if __name__ == "__main__":
    # === CONFIGURATION ===
    # Get configuration from environment
    api_key = os.getenv("PROXY_API_KEY")
    if api_key is None:
        raise ValueError("PROXY_API_KEY environment variable required")

    admin_key = os.getenv("ADMIN_API_KEY")
    if admin_key:
        logger.info("Admin API key configured (policy management enabled)")
    else:
        logger.warning("ADMIN_API_KEY not set - admin endpoints will return 500")

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable required")

    # Policy configuration
    policy_source = os.getenv("POLICY_SOURCE", "db-fallback-file")
    policy_config_path = os.getenv("POLICY_CONFIG", "config/policy_config.yaml")

    # Validate policy source
    valid_sources = ["db", "file", "db-fallback-file", "file-fallback-db"]
    if policy_source not in valid_sources:
        raise ValueError(f"Invalid POLICY_SOURCE={policy_source}. Must be one of: {', '.join(valid_sources)}")

    logger.info(f"Policy configuration: source={policy_source}, path={policy_config_path}")

    # Create app with factory function
    app = create_app(
        api_key=api_key,
        admin_key=admin_key,
        database_url=database_url,
        redis_url=redis_url,
        policy_source=policy_source,
        policy_config_path=policy_config_path,
    )

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
