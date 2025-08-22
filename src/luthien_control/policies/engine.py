# ABOUTME: Policy engine for managing AI control policies, configurations, and decision logging
# ABOUTME: Handles policy loading, episode tracking, and audit triggers for the control plane

import asyncio
import json
import os
from typing import Any, Dict, Optional

import asyncpg
from beartype import beartype
import redis.asyncio as redis
import yaml


class PolicyEngine:
    """Manages AI control policies and tracks episode state."""

    def __init__(
        self, database_url: Optional[str] = None, redis_url: Optional[str] = None
    ):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.db_pool: Optional[asyncpg.Pool] = None
        self.redis_client: Optional[redis.Redis] = None
        self.default_policy = self._load_default_policy()

    @beartype
    async def initialize(self) -> None:
        """Initialize database and Redis connections."""
        try:
            if self.database_url:
                self.db_pool = await asyncpg.create_pool(
                    self.database_url, min_size=2, max_size=10
                )
                print("Connected to PostgreSQL")

            self.redis_client = redis.from_url(self.redis_url)
            await self.redis_client.ping()
            print("Connected to Redis")

        except Exception as e:
            print(f"Warning: Could not initialize policy engine connections: {e}")
            # Continue without persistence for now

    @beartype
    def _load_default_policy(self) -> Dict[str, Any]:
        """Load default policy configuration."""
        try:
            config_path = "/app/config/policy_default.yaml"
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    return yaml.safe_load(f)
        except Exception as e:
            print(f"Could not load default policy: {e}")

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
        user_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Get policy configuration for an episode."""
        # For now, return default policy
        # TODO: Implement per-user/per-team policy selection
        return self.default_policy.copy()

    @beartype
    async def log_decision(
        self,
        episode_id: Optional[str],
        step_id: Optional[str],
        decision_type: str,
        score: float,
        threshold: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a control decision for audit purposes."""
        try:
            decision_data = {
                "episode_id": episode_id,
                "step_id": step_id,
                "decision_type": decision_type,
                "score": score,
                "threshold": threshold,
                "metadata": metadata or {},
                "timestamp": asyncio.get_event_loop().time(),
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

            print(
                f"Logged decision: {decision_type} (score: {score:.3f}, threshold: {threshold:.3f})"
            )

        except Exception as e:
            print(f"Error logging decision: {e}")

    @beartype
    async def trigger_audit(
        self,
        episode_id: str,
        step_id: str,
        reason: str,
        score: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Trigger human audit for high-risk decisions."""
        try:
            audit_data = {
                "episode_id": episode_id,
                "step_id": step_id,
                "reason": reason,
                "score": score,
                "metadata": metadata or {},
                "status": "pending",
                "created_at": asyncio.get_event_loop().time(),
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
                # Add to audit queue
                await self.redis_client.lpush("audit_queue", json.dumps(audit_data))

            print(f"Triggered audit: {reason} (episode: {episode_id})")

            # TODO: Send alerts via Slack/email/PagerDuty

        except Exception as e:
            print(f"Error triggering audit: {e}")

    @beartype
    async def get_episode_state(self, episode_id: str) -> Dict[str, Any]:
        """Get current state for an episode."""
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
                    "created_at": asyncio.get_event_loop().time(),
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
            print(f"Error getting episode state: {e}")
            return {}

    @beartype
    async def update_episode_state(
        self, episode_id: str, updates: Dict[str, Any]
    ) -> None:
        """Update episode state."""
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
            print(f"Error updating episode state: {e}")
