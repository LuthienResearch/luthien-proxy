# ABOUTME: Runtime policy management with configurable source precedence
# ABOUTME: Supports DB, file, or hybrid policy configuration with hot-reload

"""Policy manager for runtime policy configuration.

Manages policy lifecycle including:
- Loading policies from database or file with configurable precedence
- Hot-swapping policies at runtime without restart
- Persisting policy changes to database or file
- Distributed locking for concurrent policy changes
- Audit trail and history
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import yaml
from fastapi import HTTPException
from redis.asyncio import Redis

from luthien_proxy.config import _import_policy_class, _instantiate_policy, load_policy_from_yaml
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.utils import db

logger = logging.getLogger(__name__)

PolicySource = Literal["db", "file", "db-fallback-file", "file-fallback-db"]


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
    source_info: dict[str, Any]


class PolicyManager:
    """Manages runtime policy with configurable source precedence."""

    def __init__(
        self,
        db_pool: db.DatabasePool,
        redis_client: Redis,
        yaml_path: str,
        policy_source: PolicySource = "db-fallback-file",
    ):
        """Initialize PolicyManager.

        Args:
            db_pool: Database connection pool
            redis_client: Redis client for locking and caching
            yaml_path: Path to YAML policy configuration file
            policy_source: Source precedence mode
        """
        self.db = db_pool
        self.redis = redis_client
        self.yaml_path = yaml_path
        self.policy_source = policy_source
        self._current_policy: PolicyProtocol | None = None
        self.lock_key = "luthien:policy:lock"

        logger.info(f"PolicyManager initialized with source precedence: {policy_source}")

    async def initialize(self) -> None:
        """Load policy based on configured source precedence."""
        if self.policy_source == "db":
            # DB only - error if not found
            self._current_policy = await self._load_from_db(required=True)

        elif self.policy_source == "file":
            # File only - error if not found
            self._current_policy = await self._load_from_file(required=True)

        elif self.policy_source == "db-fallback-file":
            # Try DB first, fall back to file
            try:
                self._current_policy = await self._load_from_db(required=False)
                if self._current_policy:
                    logger.info("Loaded policy from database")
                else:
                    logger.info("No policy in database, loading from file")
                    self._current_policy = await self._load_from_file(required=True)
                    # Persist to DB for next time
                    await self._sync_file_to_db()
            except Exception as e:
                logger.warning(f"Failed to load from DB: {e}, falling back to file")
                self._current_policy = await self._load_from_file(required=True)

        elif self.policy_source == "file-fallback-db":
            # Try file first, fall back to DB
            try:
                self._current_policy = await self._load_from_file(required=False)
                if self._current_policy:
                    logger.info("Loaded policy from file")
                else:
                    logger.info("No policy file, loading from database")
                    self._current_policy = await self._load_from_db(required=True)
            except FileNotFoundError:
                logger.warning(f"Policy file not found at {self.yaml_path}, loading from DB")
                self._current_policy = await self._load_from_db(required=True)

        else:
            raise ValueError(f"Invalid POLICY_SOURCE: {self.policy_source}")

        if not self._current_policy:
            raise RuntimeError("Failed to load policy from any source")

    async def _load_from_db(self, required: bool = False) -> PolicyProtocol | None:
        """Load policy from database.

        Args:
            required: If True, raise error if policy not found

        Returns:
            Policy instance or None if not found (when required=False)
        """
        try:
            pool = await self.db.get_pool()
            row = await pool.fetchrow(
                """
                SELECT policy_class_ref, config
                FROM policy_config
                WHERE is_active = true
                ORDER BY enabled_at DESC
                LIMIT 1
            """
            )

            if not row:
                if required:
                    raise RuntimeError("No active policy found in database")
                return None

            policy_class_ref = str(row["policy_class_ref"])
            policy_class = _import_policy_class(policy_class_ref)
            config_value = row["config"]
            config = config_value if isinstance(config_value, dict) else json.loads(str(config_value))
            return _instantiate_policy(policy_class, config)

        except Exception as e:
            if required:
                raise RuntimeError(f"Failed to load policy from database: {e}")
            logger.warning(f"Could not load from database: {e}")
            return None

    async def _load_from_file(self, required: bool = False) -> PolicyProtocol | None:
        """Load policy from YAML file.

        Args:
            required: If True, raise error if file not found

        Returns:
            Policy instance or None if not found (when required=False)
        """
        try:
            if not os.path.exists(self.yaml_path):
                if required:
                    raise FileNotFoundError(f"Policy config not found: {self.yaml_path}")
                return None

            return load_policy_from_yaml(self.yaml_path)

        except Exception as e:
            if required:
                raise RuntimeError(f"Failed to load policy from file: {e}")
            logger.warning(f"Could not load from file: {e}")
            return None

    async def _sync_file_to_db(self) -> None:
        """Sync current file-based policy to database."""
        if not self._current_policy:
            return

        policy_class_ref = f"{self._current_policy.__module__}:{self._current_policy.__class__.__name__}"
        config = self._current_policy.get_config() if hasattr(self._current_policy, "get_config") else {}  # type: ignore

        try:
            pool = await self.db.get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Deactivate existing policies
                    await conn.execute("UPDATE policy_config SET is_active = false")

                    # Insert file-based policy
                    await conn.execute(
                        """
                        INSERT INTO policy_config
                        (policy_class_ref, config, enabled_at, enabled_by, is_active)
                        VALUES ($1, $2, NOW(), 'file-sync', true)
                    """,
                        policy_class_ref,
                        json.dumps(config),
                    )

            logger.info(f"Synced policy from file to database: {policy_class_ref}")
        except Exception as e:
            logger.warning(f"Failed to sync policy to database: {e}")

    async def enable_policy(
        self, policy_class_ref: str, config: dict[str, Any], enabled_by: str = "unknown"
    ) -> PolicyEnableResult:
        """Enable a new policy with persistence based on policy_source.

        Args:
            policy_class_ref: Full module path to policy class
            config: Configuration dictionary for policy
            enabled_by: Identifier of who enabled the policy

        Returns:
            PolicyEnableResult with success status and error details
        """
        # Check if policy changes are allowed
        if self.policy_source == "file":
            raise HTTPException(status_code=403, detail="Policy changes disabled: POLICY_SOURCE=file (read-only mode)")

        start_time = datetime.now()

        async with self._acquire_lock():
            try:
                # 1. Validate policy
                policy_class = _import_policy_class(policy_class_ref)
                new_policy = _instantiate_policy(policy_class, config)

                # 2. Persist based on source configuration
                if self.policy_source in ("db", "db-fallback-file"):
                    # Always persist to DB
                    await self._persist_to_db(policy_class_ref, config, enabled_by)

                elif self.policy_source == "file-fallback-db":
                    # Try file first, fall back to DB
                    try:
                        await self._persist_to_file(policy_class_ref, config)
                    except Exception as e:
                        logger.warning(f"Failed to write file, using DB: {e}")
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
        """Persist policy configuration to database."""
        pool = await self.db.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE policy_config SET is_active = false")
                await conn.execute(
                    """
                    INSERT INTO policy_config
                    (policy_class_ref, config, enabled_at, enabled_by, is_active)
                    VALUES ($1, $2, NOW(), $3, true)
                """,
                    policy_class_ref,
                    json.dumps(config),
                    enabled_by,
                )

    async def _persist_to_file(self, policy_class_ref: str, config: dict[str, Any]) -> None:
        """Persist policy configuration to YAML file (atomic write)."""
        yaml_content = {"policy": {"class": policy_class_ref, "config": config}}

        # Atomic write: write to temp file, then rename
        temp_path = f"{self.yaml_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.dump(yaml_content, f, default_flow_style=False)

            # Atomic rename
            os.replace(temp_path, self.yaml_path)

        finally:
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def get_current_policy(self) -> PolicyInfo:
        """Get current policy information.

        Returns:
            PolicyInfo with metadata about current policy
        """
        if not self._current_policy:
            raise RuntimeError("No policy loaded")

        # Get metadata from DB if available
        enabled_at = None
        enabled_by = None

        try:
            pool = await self.db.get_pool()
            row = await pool.fetchrow(
                """
                SELECT enabled_at, enabled_by
                FROM policy_config
                WHERE is_active = true
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
            source_info=await self.get_policy_source_info(),
        )

    async def get_policy_source_info(self) -> dict[str, Any]:
        """Get information about policy source configuration."""
        return {
            "policy_source": self.policy_source,
            "yaml_path": self.yaml_path,
            "supports_runtime_changes": self.policy_source != "file",
            "persistence_target": (
                "database"
                if self.policy_source in ("db", "db-fallback-file")
                else "file"
                if self.policy_source == "file-fallback-db"
                else "read-only"
            ),
        }

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
        acquired = await lock.acquire(blocking=True, blocking_timeout=10)
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
                    f"Check that policy config exists at: {self.yaml_path}",
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


__all__ = ["PolicyManager", "PolicyEnableResult", "PolicyInfo", "PolicySource"]
