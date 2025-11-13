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

from luthien_proxy.config import load_policy_from_yaml
from luthien_proxy.debug import router as debug_router
from luthien_proxy.gateway_routes import router as gateway_router
from luthien_proxy.observability import RedisEventPublisher
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.telemetry import setup_telemetry
from luthien_proxy.ui import router as ui_router
from luthien_proxy.utils import db

logger = logging.getLogger(__name__)


def create_app(
    api_key: str,
    database_url: str,
    redis_url: str,
    policy: PolicyProtocol,
) -> FastAPI:
    """Create FastAPI application with dependency injection.

    Args:
        api_key: API key for authentication
        database_url: PostgreSQL database URL
        redis_url: Redis URL for event publishing
        policy: Policy handler instance

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

        # Initialize event publisher for real-time UI
        _event_publisher: RedisEventPublisher | None = None
        if _redis_client:
            _event_publisher = RedisEventPublisher(_redis_client)
            logger.info("Event publisher initialized for real-time UI")
        else:
            logger.info("Event publisher disabled (no Redis)")

        # Store everything in app state for dependency injection
        app.state.db_pool = _db_pool
        app.state.redis_client = _redis_client
        app.state.event_publisher = _event_publisher
        app.state.policy = policy
        app.state.api_key = api_key
        logger.info("App state initialized")

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
    app.include_router(ui_router)  # /activity/*

    # Simple utility endpoints
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "version": "2.0.0"}

    return app


__all__ = ["create_app"]


if __name__ == "__main__":
    # === CONFIGURATION ===
    api_key = os.getenv("PROXY_API_KEY")
    if api_key is None:
        raise ValueError("PROXY_API_KEY environment variable required")

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable required")

    # Load policy from YAML configuration
    # Set POLICY_CONFIG env var to override default (config/policy_config.yaml)
    policy_handler = load_policy_from_yaml()

    # Create app with factory function
    app = create_app(
        api_key=api_key,
        database_url=database_url,
        redis_url=redis_url,
        policy=policy_handler,
    )

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
