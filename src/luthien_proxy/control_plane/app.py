"""FastAPI app for the Luthien Control Plane.

Provides endpoints that receive LiteLLM hook events, lightweight debug UIs,
and helper APIs. Policy decisions and persistence stay outside this module to
keep the web layer thin.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from contextlib import asynccontextmanager
from functools import partial
from typing import Optional, TypedDict

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from luthien_proxy.control_plane.conversation import ConversationStreamConfig
from luthien_proxy.control_plane.stream_context import StreamContextStore
from luthien_proxy.control_plane.ui import router as ui_router
from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.utils import db, redis_client
from luthien_proxy.utils.project_config import ProjectConfig
from luthien_proxy.v2.control.local import ControlPlaneLocal
from luthien_proxy.v2.observability import SimpleEventPublisher
from luthien_proxy.v2.policies.noop import NoOpPolicy as V2NoOpPolicy
from luthien_proxy.v2.routes import router as v2_router
from luthien_proxy.v2.telemetry import setup_telemetry

from .debug_records import record_debug_event
from .debug_routes import (
    DebugEntry,
    DebugPage,
    DebugTypeInfo,
    get_debug_entries,
    get_debug_page,
    get_debug_types,
)
from .debug_routes import (
    router as debug_router,
)
from .demo_routes import router as demo_router
from .dependencies import (
    DebugLogWriter,
    get_database_pool,
    get_project_config,
    get_redis_client,
)
from .hooks_routes import (
    CallIdInfo,
    get_hook_counters,
    hook_generic,
    recent_call_ids,
)
from .hooks_routes import (
    router as hooks_router,
)
from .policy_loader import load_policy_from_config
from .streaming_routes import (
    router as streaming_router,
)
from .utils.rate_limiter import RateLimiter


class HealthPayload(TypedDict):
    status: str
    service: str
    version: str


class EndpointListing(TypedDict):
    hooks: list[str]
    ui: list[str]
    health: str


logger = logging.getLogger(__name__)

router = APIRouter()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@router.get("/health")
async def health_check() -> HealthPayload:
    """Return a simple health payload without touching external services."""
    return {"status": "healthy", "service": "luthien-control-plane", "version": "0.1.0"}


@router.get("/endpoints")
async def list_endpoints() -> EndpointListing:
    """List notable HTTP endpoints for quick discoverability."""
    return {
        "hooks": [
            "POST /api/hooks/{hook_name}",
        ],
        "ui": [
            "GET /ui/conversation",
            "GET /ui/conversation/by_call",
            "GET /ui/conversation/live",
            "GET /ui/activity/live",
        ],
        "health": "GET /health",
    }


def create_control_plane_app(config: ProjectConfig) -> FastAPI:
    """Construct a FastAPI app instance configured with the provided project config."""
    hook_counters: Counter[str] = Counter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.project_config = config
        app.state.hook_counters = hook_counters

        control_cfg = config.control_plane_config
        policy: Optional[LuthienPolicy] = None
        database_pool: Optional[db.DatabasePool] = None
        redis_instance: Optional[redis_client.RedisClient] = None
        redis_manager = redis_client.RedisClientManager()
        rate_limiter: Optional[RateLimiter] = None
        stream_config: Optional[ConversationStreamConfig] = None

        # V2 components
        v2_redis_client: Optional[Redis] = None
        v2_event_publisher: Optional[SimpleEventPublisher] = None
        v2_control_plane: Optional[ControlPlaneLocal] = None

        try:
            # === V1 Control Plane Initialization ===
            app.state.conversation_rate_limiter = None
            app.state.conversation_stream_config = None
            database_pool = db.DatabasePool(control_cfg.database_url)
            await database_pool.get_pool()
            app.state.database_pool = database_pool
            debug_writer = partial(record_debug_event, database_pool)
            app.state.debug_log_writer = debug_writer

            redis_instance = await redis_manager.get_client(control_cfg.redis_url)
            app.state.redis_manager = redis_manager
            app.state.redis_client = redis_instance

            policy = load_policy_from_config(config, control_cfg.policy_config_path)
            if isinstance(policy, LuthienPolicy):
                policy.set_debug_log_writer(debug_writer)
                policy.set_database_pool(database_pool)
                policy.set_redis_client(redis_instance)

            stream_config = control_cfg.conversation_stream_config
            rate_limiter = RateLimiter(
                max_events=stream_config.rate_limit_max_requests,
                window_seconds=stream_config.rate_limit_window_seconds,
            )
            app.state.conversation_rate_limiter = rate_limiter
            app.state.conversation_stream_config = stream_config

            stream_store = StreamContextStore(
                redis_client=redis_instance,
                ttl_seconds=control_cfg.stream_context_ttl,
            )

            app.state.active_policy = policy
            app.state.stream_store = stream_store

            logger.info("V1 Control plane services initialized successfully")

            # === V2 OpenTelemetry Integration ===
            # Initialize OpenTelemetry
            setup_telemetry(app)
            logger.info("OpenTelemetry initialized for V2 endpoints")

            # Connect to Redis for V2 event publisher
            try:
                v2_redis_client = Redis.from_url(control_cfg.redis_url, decode_responses=False)
                await v2_redis_client.ping()
                logger.info("V2 Redis client connected")
            except Exception as exc:
                logger.warning(f"Failed to connect V2 Redis client: {exc}. V2 event publisher disabled.")
                v2_redis_client = None

            # Initialize V2 event publisher for real-time UI
            if v2_redis_client:
                v2_event_publisher = SimpleEventPublisher(v2_redis_client)
                logger.info("V2 event publisher initialized for real-time UI")
            else:
                v2_event_publisher = None

            # Initialize V2 control plane with NoOp policy (for now)
            v2_policy = V2NoOpPolicy()
            v2_control_plane = ControlPlaneLocal(
                policy=v2_policy,
                event_publisher=v2_event_publisher,
            )
            logger.info("V2 control plane initialized with OpenTelemetry tracing")

            # Store V2 components in app state
            app.state.v2_redis_client = v2_redis_client
            app.state.v2_event_publisher = v2_event_publisher
            app.state.v2_control_plane = v2_control_plane

            yield
        finally:
            # Cleanup V1
            if isinstance(policy, LuthienPolicy):
                policy.set_debug_log_writer(None)
                policy.set_database_pool(None)
                policy.set_redis_client(None)
            app.state.active_policy = None
            app.state.stream_store = None
            app.state.debug_log_writer = None
            app.state.database_pool = None
            app.state.redis_client = None
            app.state.redis_manager = None
            app.state.conversation_rate_limiter = None
            app.state.conversation_stream_config = None
            if database_pool is not None:
                await database_pool.close()
            await redis_manager.close_all()

            # Cleanup V2
            app.state.v2_redis_client = None
            app.state.v2_event_publisher = None
            app.state.v2_control_plane = None
            if v2_redis_client:
                await v2_redis_client.close()
                logger.info("V2 Redis client closed")

    app = FastAPI(
        title="Luthien Control Plane",
        description="AI Control policy orchestration service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(ui_router)
    app.include_router(router)
    app.include_router(debug_router)

    # Demo mode is disabled by default for security
    # Only enable in demo/presentation environments with ENABLE_DEMO_MODE=true
    if config.control_plane_config.enable_demo_mode:
        app.include_router(demo_router)

    app.include_router(hooks_router)
    app.include_router(streaming_router)

    # Mount V2 routes (OpenTelemetry-instrumented endpoints)
    app.include_router(v2_router)
    logger.info("V2 routes mounted at /v2/chat/completions and /v2/messages")

    return app


__all__ = [
    "create_control_plane_app",
    "get_project_config",
    "get_hook_counters",
    "get_database_pool",
    "get_redis_client",
    "list_endpoints",
    "health_check",
    "recent_call_ids",
    "get_debug_entries",
    "get_debug_types",
    "get_debug_page",
    "hook_generic",
    "load_policy_from_config",
    "DebugEntry",
    "DebugTypeInfo",
    "DebugPage",
    "CallIdInfo",
    "DebugLogWriter",
]
