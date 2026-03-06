"""Telemetry configuration resolution.

Precedence: env var > DB stored value > default (enabled).
This is intentionally the OPPOSITE of the auth_config pattern where DB
overrides env. Here the env var is the hard override so operators can
disable telemetry without touching the DB.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


@dataclass
class TelemetryConfig:
    """Resolved telemetry configuration (enabled state + deployment ID)."""

    enabled: bool
    deployment_id: str


async def resolve_telemetry_config(
    *,
    db_pool: DatabasePool | None,
    env_value: bool | None,
) -> TelemetryConfig:
    """Resolve telemetry config from env var and DB.

    Args:
        db_pool: Database pool (may be None in minimal setups)
        env_value: Value from USAGE_TELEMETRY env var (None if not set)

    Returns:
        Resolved telemetry configuration
    """
    db_enabled: bool | None = None
    deployment_id: str = str(uuid.uuid4())

    if db_pool is not None:
        try:
            pool = await db_pool.get_pool()
            row = await pool.fetchrow("SELECT enabled, deployment_id FROM telemetry_config WHERE id = 1")
            if row:
                db_enabled = bool(row["enabled"]) if row["enabled"] is not None else None
                deployment_id = str(row["deployment_id"])
        except Exception:
            logger.warning("Failed to read telemetry_config from DB, using defaults", exc_info=True)

    # Precedence: env var > DB > default (enabled)
    if env_value is not None:
        enabled = env_value
    elif db_enabled is not None:
        enabled = db_enabled
    else:
        enabled = True

    return TelemetryConfig(enabled=enabled, deployment_id=deployment_id)
