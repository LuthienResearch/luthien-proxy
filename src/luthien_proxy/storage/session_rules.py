"""CRUD operations for per-session rules stored in the database.

Rules are extracted once per session (e.g. from CLAUDE.md content) and persisted
so subsequent turns can load them without re-extraction.

A sentinel row (SENTINEL_RULE_NAME) is stored when extraction produces zero rules.
This lets has_rules() distinguish "never extracted" from "extracted but empty".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool

SENTINEL_RULE_NAME = "__no_rules_extracted__"


@dataclass(frozen=True)
class SessionRule:
    """A named rule extracted from session content."""

    name: str
    instruction: str


async def save_rules(db_pool: "DatabasePool", session_id: str, rules: list[SessionRule]) -> None:
    """Persist rules for a session.

    If rules is empty, inserts a sentinel row so has_rules() returns True
    (preventing repeated extraction attempts).
    """
    rows_to_insert = [
        (str(uuid.uuid4()), session_id, r.name, r.instruction) for r in rules
    ]
    if not rows_to_insert:
        rows_to_insert = [(str(uuid.uuid4()), session_id, SENTINEL_RULE_NAME, "")]

    async with db_pool.connection() as conn:
        async with conn.transaction():
            if db_pool.is_sqlite:
                for row_id, sid, name, instruction in rows_to_insert:
                    await conn.execute(
                        "INSERT INTO session_rules (id, session_id, rule_name, rule_instruction) "
                        "VALUES (?, ?, ?, ?)",
                        row_id,
                        sid,
                        name,
                        instruction,
                    )
            else:
                for row_id, sid, name, instruction in rows_to_insert:
                    await conn.execute(
                        "INSERT INTO session_rules (id, session_id, rule_name, rule_instruction) "
                        "VALUES ($1, $2, $3, $4)",
                        row_id,
                        sid,
                        name,
                        instruction,
                    )


async def load_rules(db_pool: "DatabasePool", session_id: str) -> list[SessionRule]:
    """Load rules for a session, filtering out sentinel rows."""
    if db_pool.is_sqlite:
        query = "SELECT rule_name, rule_instruction FROM session_rules WHERE session_id = ? AND rule_name != ?"
        rows = await (await db_pool.get_pool()).fetch(query, session_id, SENTINEL_RULE_NAME)
    else:
        query = "SELECT rule_name, rule_instruction FROM session_rules WHERE session_id = $1 AND rule_name != $2"
        rows = await (await db_pool.get_pool()).fetch(query, session_id, SENTINEL_RULE_NAME)

    return [SessionRule(name=str(row["rule_name"]), instruction=str(row["rule_instruction"])) for row in rows]


async def has_rules(db_pool: "DatabasePool", session_id: str) -> bool:
    """Check if rules have been extracted for this session (including sentinel)."""
    if db_pool.is_sqlite:
        query = "SELECT 1 FROM session_rules WHERE session_id = ? LIMIT 1"
        row = await (await db_pool.get_pool()).fetchrow(query, session_id)
    else:
        query = "SELECT 1 FROM session_rules WHERE session_id = $1 LIMIT 1"
        row = await (await db_pool.get_pool()).fetchrow(query, session_id)

    return row is not None


__all__ = ["SessionRule", "has_rules", "load_rules", "save_rules"]
