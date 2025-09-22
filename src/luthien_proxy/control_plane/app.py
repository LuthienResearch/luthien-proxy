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
from typing import Any, Optional

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from luthien_proxy.control_plane.stream_context import StreamContextStore
from luthien_proxy.control_plane.ui import router as ui_router
from luthien_proxy.utils import db, redis_client
from luthien_proxy.utils.project_config import ProjectConfig

from .debug_logging import insert_debug
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
from .dependencies import (
    DebugLogWriter as _DebugLogWriter,
)
from .dependencies import (
    get_database_pool as _get_database_pool,
)
from .dependencies import (
    get_project_config as _get_project_config,
)
from .dependencies import (
    get_redis_client as _get_redis_client,
)
from .hooks_routes import (
    CallIdInfo,
    TraceEntry,
    TraceResponse,
    hook_generic,
    recent_call_ids,
    trace_by_call_id,
)
from .hooks_routes import (
    get_hook_counters as _get_hook_counters,
)
from .hooks_routes import (
    router as hooks_router,
)
from .policy_loader import _load_policy_from_config

DebugLogWriter = _DebugLogWriter
get_project_config = _get_project_config
get_database_pool = _get_database_pool
get_redis_client = _get_redis_client
get_hook_counters = _get_hook_counters

logger = logging.getLogger(__name__)

router = APIRouter()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Return a simple health payload without touching external services."""
    return {"status": "healthy", "service": "luthien-control-plane", "version": "0.1.0"}


@router.get("/endpoints")
async def list_endpoints() -> dict[str, Any]:
    """List notable HTTP endpoints for quick discoverability."""
    return {
        "hooks": [
            "POST /hooks/{hook_name}",
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
        database_pool: Optional[db.DatabasePool] = None
        redis_instance: Optional[redis_client.RedisClient] = None
        redis_manager = redis_client.RedisClientManager()

        try:
            if control_cfg.database_url is not None:
                database_pool = db.DatabasePool(control_cfg.database_url)
                await database_pool.get_pool()
            app.state.database_pool = database_pool
            app.state.debug_log_writer = partial(insert_debug, database_pool)

            redis_instance = await redis_manager.get_client(control_cfg.redis_url)
            app.state.redis_manager = redis_manager
            app.state.redis_client = redis_instance

            policy = _load_policy_from_config(config, control_cfg.policy_config_path)

            stream_store = StreamContextStore(
                redis_client=redis_instance,
                ttl_seconds=control_cfg.stream_context_ttl,
            )

            app.state.active_policy = policy
            app.state.stream_store = stream_store

            logger.info("Control plane services initialized successfully")
            yield
        finally:
            app.state.active_policy = None
            app.state.stream_store = None
            app.state.debug_log_writer = None
            app.state.database_pool = None
            app.state.redis_client = None
            app.state.redis_manager = None
            if database_pool is not None:
                await database_pool.close()
            await redis_manager.close_all()

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
    app.include_router(hooks_router)

    return app


__all__ = [
    "create_control_plane_app",
    "get_project_config",
    "get_hook_counters",
    "get_database_pool",
    "get_redis_client",
    "list_endpoints",
    "health_check",
    "trace_by_call_id",
    "recent_call_ids",
    "get_debug_entries",
    "get_debug_types",
    "get_debug_page",
    "hook_generic",
    "_load_policy_from_config",
    "DebugEntry",
    "DebugTypeInfo",
    "DebugPage",
    "TraceEntry",
    "TraceResponse",
    "CallIdInfo",
    "DebugLogWriter",
]
