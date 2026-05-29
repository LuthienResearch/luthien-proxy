"""Service layer for user labels — human-readable names for opaque user_ids.

A ``user_id`` is the attribution token extracted from the X-Luthien-User-Id
header or a JWT ``sub`` claim (see :mod:`luthien_proxy.pipeline.session`). It is
opaque and not meant to be read by a human, so the history UI lets an operator
attach a display name to one. These functions back the ``/api/history`` label
endpoints; they are pure DB logic with no FastAPI dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone

from luthien_proxy.utils.db import DatabasePool

# Display-name length bound. Enforced at the request boundary by the route's
# UserLabelRequest Pydantic model (max_length); this constant is the single
# source of truth that model imports. The service intentionally does NOT
# re-check length — length is a boundary/storage concern owned by the route,
# while set_label owns only the semantic non-blank invariant (which Pydantic
# can't express). The column itself has no length limit in the DDL.
MAX_DISPLAY_NAME_LENGTH = 255


async def list_labels(db_pool: DatabasePool) -> dict[str, str]:
    """Return all labels as a ``{user_id: display_name}`` mapping."""
    async with db_pool.connection() as conn:
        rows = await conn.fetch("SELECT user_id, display_name FROM user_labels ORDER BY display_name")
    return {str(row["user_id"]): str(row["display_name"]) for row in rows}


async def list_users(db_pool: DatabasePool, *, limit: int = 500, offset: int = 0) -> dict[str, object]:
    """List distinct user_ids seen across sessions, plus any assigned labels.

    Reads ``session_summaries`` (one row per session, indexed on user_id)
    rather than ``conversation_calls`` (one row per call) so the DISTINCT scan
    stays small on deployments with many calls per session.

    Returns ``{"users": [user_id, ...], "labels": {user_id: display_name}}``.

    Single query with a fixed two-placeholder count (limit, offset): the paged
    distinct users are LEFT JOINed to ``user_labels`` instead of fetched and
    then re-queried with one placeholder per user. The old N-placeholder
    ``WHERE user_id IN (...)`` form could exceed SQLite's
    ``SQLITE_MAX_VARIABLE_NUMBER`` (999 on older builds) once a deployment had
    more distinct users than that on a single page.
    """
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT s.user_id, ul.display_name
            FROM (
                SELECT DISTINCT user_id FROM session_summaries
                WHERE user_id IS NOT NULL
                ORDER BY user_id
                LIMIT $1 OFFSET $2
            ) s
            LEFT JOIN user_labels ul ON ul.user_id = s.user_id
            ORDER BY s.user_id
            """,
            limit,
            offset,
        )
    user_ids = [str(row["user_id"]) for row in rows]
    labels = {str(row["user_id"]): str(row["display_name"]) for row in rows if row["display_name"] is not None}
    return {"users": user_ids, "labels": labels}


async def set_label(db_pool: DatabasePool, user_id: str, display_name: str) -> str:
    """Create or update the display name for ``user_id``.

    Returns the stored (stripped) display name.

    Raises:
        ValueError: if ``display_name`` is blank after stripping.
    """
    cleaned = display_name.strip()
    if not cleaned:
        raise ValueError("display_name must not be blank")
    now = datetime.now(timezone.utc)
    async with db_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO user_labels (user_id, display_name, created_at, updated_at)
            VALUES ($1, $2, $3, $3)
            ON CONFLICT (user_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                updated_at = EXCLUDED.updated_at
            """,
            user_id,
            cleaned,
            now,
        )
    return cleaned


async def delete_label(db_pool: DatabasePool, user_id: str) -> None:
    """Remove the label for ``user_id`` (no-op if none exists)."""
    async with db_pool.connection() as conn:
        await conn.execute("DELETE FROM user_labels WHERE user_id = $1", user_id)


__all__ = [
    "MAX_DISPLAY_NAME_LENGTH",
    "list_labels",
    "list_users",
    "set_label",
    "delete_label",
]
