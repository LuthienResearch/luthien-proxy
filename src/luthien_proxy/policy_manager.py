"""Policy manager for runtime policy configuration.

Manages policy lifecycle including:
- Loading policy from database at startup
- Optional override from YAML file at startup (persisted to DB)
- Hot-swapping policies at runtime without restart
- Distributed locking for concurrent policy changes
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from redis.asyncio import Redis

from luthien_proxy.config import _import_policy_class, _instantiate_policy, load_policy_from_yaml
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.utils import db
from luthien_proxy.utils.constants import REDIS_LOCK_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class PolicyEnableResult:
    """Result of enabling a policy."""

    success: bool
    policy: str | None = None
    error: str | None = None
    troubleshooting: list[str] | None = None
    restart_duration_ms: int | None = None


@dataclass
class PolicyInfo:
    """Current policy information."""

    policy: str
    class_ref: str
    enabled_at: str | None
    enabled_by: str | None
    config: dict[str, Any]


class PolicyManager:
    """Manages runtime policy with simplified initialization.

    Startup behavior:
    1. If startup_policy_path is provided, load from that YAML and persist to DB
    2. Otherwise, load from DB (the current_policy table)
    3. If neither has a policy, fail startup with clear error
    """

    def __init__(
        self,
        db_pool: db.DatabasePool,
        redis_client: Redis,
        startup_policy_path: str | None = None,
    ):
        """Initialize PolicyManager.

        Args:
            db_pool: Database connection pool
            redis_client: Redis client for locking
            startup_policy_path: Optional path to YAML policy config to use at startup
                                 (overrides DB, and persists to DB)
        """
        self.db = db_pool
        self.redis = redis_client
        self.startup_policy_path = startup_policy_path
        self._current_policy: PolicyProtocol | None = None
        self.lock_key = "luthien:policy:lock"

        logger.info(
            "PolicyManager initialized"
            + (f" with startup override: {startup_policy_path}" if startup_policy_path else "")
        )

    async def initialize(self) -> None:
        """Load policy at startup.

        Priority:
        1. If startup_policy_path is set, load from file and persist to DB
        2. Otherwise, load from DB
        3. If no policy found, raise error
        """
        if self.startup_policy_path:
            await self._initialize_from_file()
        else:
            await self._initialize_from_db()

        if not self._current_policy:
            raise RuntimeError(
                "No policy configured. Either set POLICY_CONFIG to a YAML file path, "
                "or configure a policy via the admin API first."
            )

    async def _initialize_from_file(self) -> None:
        """Load policy from YAML file and persist to DB."""
        if not self.startup_policy_path or not os.path.exists(self.startup_policy_path):
            raise FileNotFoundError(f"Policy config not found: {self.startup_policy_path}")

        self._current_policy = load_policy_from_yaml(self.startup_policy_path)

        # Extract class ref and config for DB persistence
        policy_class_ref = f"{self._current_policy.__module__}:{self._current_policy.__class__.__name__}"
        config: dict[str, Any] = {}
        if hasattr(self._current_policy, "get_config") and callable(getattr(self._current_policy, "get_config")):
            config = getattr(self._current_policy, "get_config")()

        await self._persist_to_db(policy_class_ref, config, "startup")
        logger.info(f"Loaded policy from {self.startup_policy_path} and persisted to DB")

    async def _initialize_from_db(self) -> None:
        """Load policy from database."""
        self._current_policy = await self._load_from_db()
        if self._current_policy:
            logger.info("Loaded policy from database")

    async def _load_from_db(self) -> PolicyProtocol | None:
        """Load policy from database.

        Returns:
            Policy instance or None if not found
        """
        try:
            pool = await self.db.get_pool()
            row = await pool.fetchrow(
                """
                SELECT policy_class_ref, config
                FROM current_policy
                WHERE id = 1
            """
            )

            if not row:
                return None

            policy_class_ref = str(row["policy_class_ref"])
            policy_class = _import_policy_class(policy_class_ref)
            config_value = row["config"]
            config = config_value if isinstance(config_value, dict) else json.loads(str(config_value))
            return _instantiate_policy(policy_class, config)

        except Exception as e:
            logger.warning(f"Could not load from database: {e}")
            return None

    async def enable_policy(
        self, policy_class_ref: str, config: dict[str, Any], enabled_by: str = "unknown"
    ) -> PolicyEnableResult:
        """Enable a new policy with persistence to DB.

        Args:
            policy_class_ref: Full module path to policy class
            config: Configuration dictionary for policy
            enabled_by: Identifier of who enabled the policy

        Returns:
            PolicyEnableResult with success status and error details
        """
        start_time = datetime.now()

        async with self._acquire_lock():
            try:
                # 1. Validate policy
                policy_class = _import_policy_class(policy_class_ref)
                new_policy = _instantiate_policy(policy_class, config)

                # 2. Persist to DB
                await self._persist_to_db(policy_class_ref, config, enabled_by)

                # 3. Hot-swap in memory
                old_policy = self._current_policy
                self._current_policy = new_policy

                # 4. Cleanup old policy
                if old_policy and hasattr(old_policy, "on_session_end"):
                    try:
                        await old_policy.on_session_end()  # type: ignore
                    except Exception as e:
                        logger.warning(f"Error during old policy cleanup: {e}")

                duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

                logger.info(f"Policy enabled: {policy_class_ref} by {enabled_by} (took {duration_ms}ms)")

                return PolicyEnableResult(success=True, policy=policy_class_ref, restart_duration_ms=duration_ms)

            except Exception as e:
                logger.error(f"Failed to enable policy: {e}", exc_info=True)
                return PolicyEnableResult(
                    success=False, error=str(e), troubleshooting=self._generate_troubleshooting(e)
                )

    async def _persist_to_db(self, policy_class_ref: str, config: dict[str, Any], enabled_by: str) -> None:
        """Persist policy configuration to database (upsert)."""
        pool = await self.db.get_pool()
        await pool.execute(
            """
            INSERT INTO current_policy (id, policy_class_ref, config, enabled_at, enabled_by)
            VALUES (1, $1, $2, NOW(), $3)
            ON CONFLICT (id) DO UPDATE SET
                policy_class_ref = EXCLUDED.policy_class_ref,
                config = EXCLUDED.config,
                enabled_at = EXCLUDED.enabled_at,
                enabled_by = EXCLUDED.enabled_by
        """,
            policy_class_ref,
            json.dumps(config),
            enabled_by,
        )

    async def get_current_policy(self) -> PolicyInfo:
        """Get current policy information.

        Returns:
            PolicyInfo with metadata about current policy
        """
        if not self._current_policy:
            raise RuntimeError("No policy loaded")

        # Get metadata from DB
        enabled_at = None
        enabled_by = None

        try:
            pool = await self.db.get_pool()
            row = await pool.fetchrow(
                """
                SELECT enabled_at, enabled_by
                FROM current_policy
                WHERE id = 1
            """
            )
            if row:
                enabled_at_value = row["enabled_at"]
                if not isinstance(enabled_at_value, datetime):
                    raise TypeError(f"enabled_at must be datetime, got {type(enabled_at_value)}")
                enabled_at = enabled_at_value.isoformat()
                enabled_by_value = row["enabled_by"]
                enabled_by = str(enabled_by_value) if enabled_by_value else None
        except Exception as e:
            logger.warning(f"Could not fetch policy metadata from DB: {e}")

        # Get config if the policy has a get_config method
        config = {}
        if hasattr(self._current_policy, "get_config") and callable(getattr(self._current_policy, "get_config")):
            config = getattr(self._current_policy, "get_config")()

        return PolicyInfo(
            policy=self._current_policy.__class__.__name__,
            class_ref=f"{self._current_policy.__module__}:{self._current_policy.__class__.__name__}",
            enabled_at=enabled_at,
            enabled_by=enabled_by,
            config=config,
        )

    @property
    def current_policy(self) -> PolicyProtocol:
        """Access current policy instance.

        Returns:
            Current active policy instance

        Raises:
            RuntimeError: If no policy is loaded
        """
        if not self._current_policy:
            raise RuntimeError("No policy loaded")
        return self._current_policy

    @asynccontextmanager
    async def _acquire_lock(self, timeout: int = 30):
        """Acquire distributed lock via Redis.

        Args:
            timeout: Lock timeout in seconds

        Yields:
            None when lock is acquired

        Raises:
            Exception: If lock cannot be acquired
        """
        lock = self.redis.lock(self.lock_key, timeout=timeout)
        acquired = await lock.acquire(blocking=True, blocking_timeout=REDIS_LOCK_TIMEOUT_SECONDS)
        if not acquired:
            raise HTTPException(status_code=503, detail="Another policy change in progress, please try again")
        try:
            yield
        finally:
            try:
                await lock.release()
            except Exception as e:
                logger.warning(f"Failed to release lock: {e}")

    def _generate_troubleshooting(self, error: Exception) -> list[str]:
        """Generate troubleshooting steps based on error type."""
        error_str = str(error).lower()

        troubleshooting = []

        if "import" in error_str or "module" in error_str:
            troubleshooting.extend(
                [
                    "Check that the policy class reference is correct",
                    "Verify the policy module exists and is importable",
                    "Example format: 'luthien_proxy.policies.all_caps_policy:AllCapsPolicy'",
                ]
            )

        if "database" in error_str or "connection" in error_str:
            troubleshooting.extend(
                [
                    "Check that the database is accessible",
                    "Verify DATABASE_URL is correct",
                    "Try: docker compose ps to check database status",
                ]
            )

        if "file" in error_str or "yaml" in error_str:
            troubleshooting.extend(
                [
                    "Check that policy config exists at the specified path",
                    "Verify YAML syntax is correct",
                    "Ensure the file is readable by the gateway process",
                ]
            )

        if not troubleshooting:
            troubleshooting = [
                "Check gateway logs for detailed error information",
                "Verify policy configuration is valid",
                "Try restarting the gateway: docker compose restart gateway",
            ]

        return troubleshooting


__all__ = ["PolicyManager", "PolicyEnableResult", "PolicyInfo"]
