"""ABOUTME: Policy event recording and retrieval.

ABOUTME: Stores policy decisions and actions linked to conversation events.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db, redis_client

logger = logging.getLogger(__name__)


async def record_policy_event(
    pool: db.DatabasePool | None,
    *,
    call_id: str,
    policy_class: str,
    event_type: str,
    policy_config: JSONObject | None = None,
    original_event_id: str | None = None,
    modified_event_id: str | None = None,
    metadata: JSONObject | None = None,
    timestamp: datetime | None = None,
) -> None:
    """Record a policy event to the database."""
    if pool is None:
        return

    if timestamp is None:
        timestamp = datetime.now(datetime.UTC if hasattr(datetime, "UTC") else None)  # type: ignore

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO policy_events (
                call_id,
                policy_class,
                policy_config,
                event_type,
                original_event_id,
                modified_event_id,
                metadata,
                created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            call_id,
            policy_class,
            json.dumps(policy_config) if policy_config else None,
            event_type,
            original_event_id,
            modified_event_id,
            json.dumps(metadata) if metadata else None,
            timestamp,
        )


async def load_policy_events(
    call_id: str,
    pool: Optional[db.DatabasePool],
) -> list[dict[str, object]]:
    """Load policy events for a call."""
    if pool is None:
        return []

    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id,
                       call_id,
                       policy_class,
                       policy_config,
                       event_type,
                       original_event_id,
                       modified_event_id,
                       metadata,
                       created_at
                FROM policy_events
                WHERE call_id = $1
                ORDER BY created_at ASC
                """,
                call_id,
            )

            results: list[dict[str, object]] = []
            for row in rows:
                # Parse JSON fields if they're strings
                policy_config = row.get("policy_config")
                if isinstance(policy_config, str):
                    try:
                        policy_config = json.loads(policy_config)
                    except json.JSONDecodeError:
                        policy_config = None

                metadata = row.get("metadata")
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = None

                results.append(
                    {
                        "id": str(row.get("id")),
                        "call_id": row.get("call_id"),
                        "policy_class": row.get("policy_class"),
                        "policy_config": policy_config,
                        "event_type": row.get("event_type"),
                        "original_event_id": str(row.get("original_event_id"))
                        if row.get("original_event_id")
                        else None,
                        "modified_event_id": str(row.get("modified_event_id"))
                        if row.get("modified_event_id")
                        else None,
                        "metadata": metadata,
                        "created_at": row.get("created_at"),
                    }
                )
            return results
    except Exception as exc:
        logger.error("Failed to load policy events for call %s: %s", call_id, exc)
        return []


async def publish_policy_event_to_activity_stream(
    redis: redis_client.RedisClient,
    *,
    call_id: str,
    policy_class: str,
    event_type: str,
    policy_config: JSONObject | None = None,
    metadata: JSONObject | None = None,
) -> None:
    """Publish a policy event to the global activity stream.

    This makes policy decisions visible in real-time monitoring.
    """
    from luthien_proxy.control_plane.activity_stream import ActivityEvent, publish_activity_event
    from luthien_proxy.control_plane.utils.task_queue import CONVERSATION_EVENT_QUEUE

    # Extract policy name from class path
    policy_name = policy_class.split(":")[-1] if ":" in policy_class else policy_class

    # Build human-readable summary
    summary_parts = [f"Policy '{policy_name}' {event_type}"]
    if metadata:
        # Add key metadata to summary
        if "action" in metadata:
            summary_parts.append(f"- {metadata['action']}")
        if "reason" in metadata:
            summary_parts.append(f"({metadata['reason']})")

    summary = " ".join(summary_parts)

    activity_event = ActivityEvent(
        timestamp=datetime.now(datetime.UTC if hasattr(datetime, "UTC") else None).isoformat(),  # type: ignore
        event_type="policy_action",
        call_id=call_id,
        trace_id=None,
        hook=None,
        summary=summary,
        payload={
            "policy_class": policy_class,
            "policy_event_type": event_type,
            "policy_config": policy_config,
            "metadata": metadata,
        },
    )

    CONVERSATION_EVENT_QUEUE.submit(publish_activity_event(redis, activity_event))


__all__ = ["record_policy_event", "load_policy_events", "publish_policy_event_to_activity_stream"]
