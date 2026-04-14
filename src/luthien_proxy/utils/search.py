"""Dialect-agnostic helpers for conversation-event full-text search.

Postgres uses the ``conversation_events.search_vector`` tsvector column (see
migration 014). SQLite uses the ``conversation_events_fts`` FTS5 virtual
table. Callers get one SQL fragment that does the right thing per backend
and never branch on ``is_postgres`` themselves.
"""

from __future__ import annotations

from luthien_proxy.utils.db import DatabasePool


def session_fts_filter_sql(pool: DatabasePool, placeholder: str) -> str:
    """Return a SQL predicate filtering ``conversation_events ce`` by FTS match.

    Args:
        pool: The database pool, used only to dispatch on dialect.
        placeholder: The bound-parameter placeholder for the FTS query string.
            Callers pass whatever their query uses (e.g. ``"$3"`` for the third
            asyncpg parameter). The placeholder is inlined verbatim; do not pass
            user input here.

    Returns:
        A SQL fragment referencing the alias ``ce`` (i.e. the query must use
        ``FROM conversation_events ce``) that matches events whose full-text
        index contains the query bound to ``placeholder``.
    """
    if pool.is_sqlite:
        return (
            f"ce.id IN (SELECT event_id FROM conversation_events_fts WHERE conversation_events_fts MATCH {placeholder})"
        )
    return f"ce.search_vector @@ plainto_tsquery('english', {placeholder})"


__all__ = ["session_fts_filter_sql"]
