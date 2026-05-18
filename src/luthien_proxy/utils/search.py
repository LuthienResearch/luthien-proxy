"""Dialect-agnostic helpers for conversation-event full-text search.

Postgres uses the ``conversation_events.search_vector`` tsvector column (see
migration 014). SQLite uses the ``conversation_events_fts`` FTS5 virtual
table. Callers use :func:`session_fts_filter_sql` to get a dialect-correct
SQL predicate plus a sanitized bind value and never branch on
``is_postgres`` themselves.

Parity goals between backends:
* Stemming: Postgres `to_tsvector('english', ...)` + `plainto_tsquery('english', ...)`
  stems English words (running/runs/ran -> run). SQLite uses the FTS5 `porter`
  tokenizer to match.
* Query semantics: Postgres `plainto_tsquery` treats input as a conjunction of
  terms, ignoring operator-ish characters. The SQLite side mirrors this by
  splitting the user's query on whitespace and quoting each token as an FTS5
  phrase; the space between phrases is implicit AND. FTS5 special characters
  (``'``, ``-``, ``+``, ``"``, ``:``) are made safe inside the phrase quoting.
* Empty queries produce zero matches on both sides.
"""

from __future__ import annotations

from luthien_proxy.utils.db import DatabasePool


def _fts5_query_from_user_input(query: str) -> str:
    """Translate free-text user input into a safe FTS5 MATCH expression.

    Each whitespace-separated token is wrapped in double-quotes (doubled-up
    internal quotes) and joined by spaces, which FTS5 treats as an implicit
    AND across phrase queries. This:

    * Escapes every FTS5 special character (``'``, ``-``, ``+``, ``"``, ``:``,
      column-filter prefixes) so they cannot crash the parser.
    * Matches the conjunction-of-terms behavior of Postgres
      ``plainto_tsquery``.

    Returns an empty phrase (``""``) for blank input; MATCH against an empty
    phrase yields no rows, mirroring ``plainto_tsquery('')``.
    """
    tokens = query.split()
    if not tokens:
        return '""'
    return " ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def session_fts_filter_sql(
    pool: DatabasePool,
    query: str,
    *,
    placeholder: str,
) -> tuple[str, str]:
    """Return ``(sql_fragment, bind_value)`` for a full-text filter on conversation events.

    Args:
        pool: Pool used only to dispatch on dialect.
        query: The raw user query. The helper takes ownership of sanitizing it
            for the chosen backend -- callers MUST pass the user-provided value
            here, not a pre-formatted query.
        placeholder: The bound-parameter placeholder as it will appear in the
            caller's final SQL (e.g. ``"$3"`` for the third asyncpg parameter).
            The placeholder is inlined into the fragment verbatim; pass a
            literal that your query-builder controls -- never a format string
            derived from user input.

    Returns:
        ``(sql_fragment, bind_value)``:

        * ``sql_fragment`` references the alias ``ce`` (i.e. the caller's query
          must use ``FROM conversation_events ce``) and matches events whose
          full-text index contains the query bound to ``placeholder``.
        * ``bind_value`` is the string the caller should append to its
          parameter list at the position matching ``placeholder``.
    """
    if pool.is_sqlite:
        fragment = (
            f"ce.id IN (SELECT event_id FROM conversation_events_fts WHERE conversation_events_fts MATCH {placeholder})"
        )
        return fragment, _fts5_query_from_user_input(query)
    fragment = f"ce.search_vector @@ plainto_tsquery('english', {placeholder})"
    return fragment, query


__all__ = ["session_fts_filter_sql"]
