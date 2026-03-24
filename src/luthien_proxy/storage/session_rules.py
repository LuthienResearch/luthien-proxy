"""CRUD operations for per-session rules stored in the database.

Rules are extracted once per session (e.g., from CLAUDE.md on the first turn)
and loaded on subsequent turns to avoid re-extraction.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRule:
    """A single rewriting rule associated with a session."""

    name: str
    instruction: str


SENTINEL_RULE_NAME = "__no_rules_extracted__"


async def save_rules(db_pool: "DatabasePool", session_id: str, rules: list[SessionRule]) -> None:
    """Persist extracted rules for a session.

    When rules is empty, inserts a sentinel row so has_rules() returns True
    on future turns, preventing repeated extraction attempts.
    """
    rows_to_insert = rules if rules else [SessionRule(name=SENTINEL_RULE_NAME, instruction="")]

    pool = await db_pool.get_pool()

    if db_pool.is_sqlite:
        async with pool.acquire() as conn:
            for rule in rows_to_insert:
                rule_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO session_rules (id, session_id, rule_name, rule_instruction) VALUES (?, ?, ?, ?)",
                    rule_id,
                    session_id,
                    rule.name,
                    rule.instruction,
                )
    else:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for rule in rows_to_insert:
                    await conn.execute(
                        "INSERT INTO session_rules (session_id, rule_name, rule_instruction) VALUES ($1, $2, $3)",
                        session_id,
                        rule.name,
                        rule.instruction,
                    )

    logger.info("Saved %d rules for session %s", len(rules), session_id[:12])


async def load_rules(db_pool: "DatabasePool", session_id: str) -> list[SessionRule]:
    """Load all rules for a session. Returns empty list if none exist."""
    pool = await db_pool.get_pool()

    if db_pool.is_sqlite:
        rows = await pool.fetch(
            "SELECT rule_name, rule_instruction FROM session_rules WHERE session_id = ? ORDER BY created_at",
            session_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT rule_name, rule_instruction FROM session_rules WHERE session_id = $1 ORDER BY created_at",
            session_id,
        )

    return [
        SessionRule(name=str(row["rule_name"]), instruction=str(row["rule_instruction"]))
        for row in rows
        if str(row["rule_name"]) != SENTINEL_RULE_NAME
    ]


async def has_rules(db_pool: "DatabasePool", session_id: str) -> bool:
    """Check whether rules have already been extracted for this session."""
    pool = await db_pool.get_pool()

    if db_pool.is_sqlite:
        row = await pool.fetchrow(
            "SELECT 1 FROM session_rules WHERE session_id = ? LIMIT 1",
            session_id,
        )
    else:
        row = await pool.fetchrow(
            "SELECT 1 FROM session_rules WHERE session_id = $1 LIMIT 1",
            session_id,
        )

    return row is not None


__all__ = ["SessionRule", "has_rules", "load_rules", "save_rules"]
