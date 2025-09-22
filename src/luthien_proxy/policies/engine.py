"""Engine for policy defaults, audit logging, and episode state (Redis/DB)."""

import json
import logging
import time
from typing import Any, Optional, cast

import redis.asyncio as redis
from beartype import beartype

from luthien_proxy.utils import db

logger = logging.getLogger(__name__)


# TODO: consolidate db interactions with a persistant session/client
# TODO: why is PolicyEngine providing  db conns as a public method?
class PolicyEngine:
    """Policy engine coordinating persistence and audit for control decisions.

    Why: Centralize policy-related persistence and episode state so the
    FastAPI layer stays thin. This class is intentionally small and explicit
    to keep mental overhead low.
    """

    def __init__(
        self,
        database_url: str,
        redis_url: str,
        pool_factory: db.PoolFactory | None = None,
    ):
        """Initialize with explicit connection URLs."""
        self.database_url = database_url
        self.redis_url = redis_url
        self.db_pool: Optional[Any] = None
        self.redis_client: Optional[redis.Redis] = None
        self.default_policy = self._load_default_policy()
        self._pool_factory = pool_factory

    @beartype
    async def initialize(self) -> None:
        """Initialize database and Redis connections.

        Best-effort: if connections fail, the engine remains usable in an
        in-memory mode (no persistence). Callers should not rely on side
        effects beyond printing a warning.
        # TODO: remove dumbass 'best effort' behavior' and fail hard if DB/Redis are
        # not available.
        """
        try:
            if self.database_url:
                self.db_pool = await db.create_pool(
                    factory=self._pool_factory,
                    url=self.database_url,
                    min_size=2,
                    max_size=10,
                )
                logger.info("Connected to PostgreSQL")

            self.redis_client = redis.from_url(self.redis_url)
            assert self.redis_client is not None
            await self.redis_client.ping()
            logger.info("Connected to Redis")

        except Exception as e:
            logger.warning("Could not initialize policy engine connections: %s", e)
            # Continue without persistence for now

    @beartype
    def _load_default_policy(self) -> dict[str, Any]:
        """Return the built-in default engine policy.

        Motivation: Avoid external policy files for engine defaults. Policy
        selection still happens via the app-level YAML; this default is only
        for engine internals (streaming thresholds, audit toggles, etc.).
        """
        # Fallback default policy
        return {
            "name": "default",
            "version": "0.1.0",
            "defer_threshold_T": 0.7,
            "streaming": {"max_chars_before_check": 400, "halt_threshold": 0.9},
            "resample": {
                "enabled": True,
                "k": 3,
                "trigger_threshold": 0.6,
                "audit_if_min_score_gt": 0.85,
            },
            "untrusted_monitor": {
                "enabled": False,  # Start with trusted-only
                "honeypots": {"upfront_fraction": 0.15, "shutdown_threshold_fnr": 0.2},
            },
        }

    @beartype
    async def get_policy(
        self,
        episode_id: Optional[str] = None,
        user_metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Return policy configuration for an episode.

        Currently returns the built-in defaults. In the future, this may look
        up team/user specific configuration.
        """
        return self.default_policy.copy()

    @beartype
    async def log_decision(
        self,
        episode_id: Optional[str],
        step_id: Optional[str],
        decision_type: str,
        score: float,
        threshold: float,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log a control decision for audit purposes.

        Persists to PostgreSQL when configured and mirrors a JSON record in
        Redis for quick inspection.
        """
        try:
            decision_data = {
                "episode_id": episode_id,
                "step_id": step_id,
                "decision_type": decision_type,
                "score": score,
                "threshold": threshold,
                "metadata": metadata or {},
                # Wall-clock timestamp for external logs; DB uses NOW().
                "timestamp": time.time(),
            }

            if self.db_pool:
                # Store in database
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO control_decisions
                        (episode_id, step_id, decision_type, score, threshold, metadata, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    """,
                        episode_id,
                        step_id,
                        decision_type,
                        score,
                        threshold,
                        json.dumps(metadata or {}),
                    )

            if self.redis_client:
                # Also store in Redis for quick access
                key = f"decision:{episode_id}:{step_id}"
                await self.redis_client.setex(
                    key,
                    3600,  # 1 hour TTL
                    json.dumps(decision_data),
                )

            logger.info(
                "Logged decision: %s (score: %.3f, threshold: %.3f)",
                decision_type,
                score,
                threshold,
            )

        except Exception as e:
            logger.error("Error logging decision: %s", e)

    @beartype
    async def trigger_audit(
        self,
        episode_id: str,
        step_id: str,
        reason: str,
        score: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Trigger human audit for high-risk decisions.

        Queues a request in Redis and, if available, writes an audit row in
        PostgreSQL. Alerting hooks can subscribe to the Redis queue.
        """
        try:
            audit_data = {
                "episode_id": episode_id,
                "step_id": step_id,
                "reason": reason,
                "score": score,
                "metadata": metadata or {},
                "status": "pending",
                # Keep human-readable epoch for Redis record.
                "created_at": time.time(),
            }

            if self.db_pool:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO audit_requests
                        (episode_id, step_id, reason, score, metadata, status, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    """,
                        episode_id,
                        step_id,
                        reason,
                        score,
                        json.dumps(metadata or {}),
                        "pending",
                    )

            if self.redis_client:
                # Add to audit queue (typing: cast to avoid stub issues)
                await cast(Any, self.redis_client).lpush("audit_queue", json.dumps(audit_data))

            logger.info("Triggered audit: %s (episode: %s)", reason, episode_id)

        except Exception as e:
            logger.error("Error triggering audit: %s", e)

    @beartype
    async def get_episode_state(self, episode_id: str) -> dict[str, Any]:
        """Return current state for an episode from Redis.

        Initializes a new state on first access with a 24h TTL.
        """
        if not self.redis_client:
            return {}

        try:
            state_key = f"episode:{episode_id}"
            state_data = await self.redis_client.get(state_key)

            if state_data:
                return json.loads(state_data)
            else:
                # Initialize new episode
                initial_state = {
                    "episode_id": episode_id,
                    "created_at": time.time(),
                    "step_count": 0,
                    "interventions": 0,
                    "audit_budget_used": 0,
                }

                await self.redis_client.setex(
                    state_key,
                    86400,  # 24 hour TTL
                    json.dumps(initial_state),
                )

                return initial_state

        except Exception as e:
            logger.error("Error getting episode state: %s", e)
            return {}

    @beartype
    async def update_episode_state(self, episode_id: str, updates: dict[str, Any]) -> None:
        """Update and persist episode state in Redis with TTL.

        This merges keys shallowly; callers should provide complete values for
        nested structures.
        """
        if not self.redis_client:
            return

        try:
            current_state = await self.get_episode_state(episode_id)
            current_state.update(updates)

            state_key = f"episode:{episode_id}"
            await self.redis_client.setex(
                state_key,
                86400,  # 24 hour TTL
                json.dumps(current_state),
            )

        except Exception as e:
            logger.error("Error updating episode state: %s", e)
