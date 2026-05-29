"""Unit tests for session_summaries incremental maintenance.

Extraction helpers are tested directly; the upsert is tested against a real
in-memory SQLite DatabasePool (the same SQL also runs on Postgres via the
shared connection abstraction).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from luthien_proxy.observability.session_summary import (
    PREVIEW_MAX_LENGTH,
    _is_policy_event,
    extract_model,
    extract_preview,
    update_session_summary,
)
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def pool() -> DatabasePool:
    p = DatabasePool("sqlite://:memory:")
    await check_migrations(p)
    return p


def _request(model: str = "claude-x", text: str = "hello", max_tokens: int | None = 100) -> dict:
    req: dict = {"messages": [{"role": "user", "content": text}]}
    if max_tokens is not None:
        req["max_tokens"] = max_tokens
    return {"final_model": model, "final_request": req}


class TestIsPolicyEvent:
    def test_policy_event_counts(self) -> None:
        assert _is_policy_event("policy.block") is True

    def test_judge_evaluation_excluded(self) -> None:
        assert _is_policy_event("policy.judge.evaluation") is False
        assert _is_policy_event("policy.judge.evaluation.completed") is False

    def test_non_policy_excluded(self) -> None:
        assert _is_policy_event("transaction.request_recorded") is False
        assert _is_policy_event("pipeline.client_request") is False


class TestExtractModel:
    def test_extracts_final_model(self) -> None:
        assert extract_model({"final_model": "claude-x"}) == "claude-x"

    def test_missing_returns_none(self) -> None:
        assert extract_model({}) is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_model({"final_model": ""}) is None


class TestExtractPreview:
    def test_extracts_first_user_message_string(self) -> None:
        assert extract_preview(_request(text="hi there")) == "hi there"

    def test_extracts_from_content_blocks(self) -> None:
        data = {
            "final_request": {
                "max_tokens": 100,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "block text"}]}],
            }
        }
        assert extract_preview(data) == "block text"

    def test_probe_request_skipped(self) -> None:
        assert extract_preview(_request(text="probe", max_tokens=1)) is None

    def test_strips_system_reminder(self) -> None:
        data = _request(text="real question <system-reminder>noise</system-reminder>")
        assert extract_preview(data) == "real question"

    def test_truncates_long_text(self) -> None:
        data = _request(text="x" * 500)
        preview = extract_preview(data)
        assert preview is not None
        assert preview.endswith("...")
        assert len(preview) == PREVIEW_MAX_LENGTH + 3

    def test_no_user_message_returns_none(self) -> None:
        data = {"final_request": {"messages": [{"role": "assistant", "content": "hi"}]}}
        assert extract_preview(data) is None

    def test_missing_request_returns_none(self) -> None:
        # Neither final_request nor original_request present.
        assert extract_preview({}) is None
        assert extract_preview({"final_request": "not-a-dict"}) is None

    def test_non_string_content_block_text_ignored(self) -> None:
        # A malformed content block where 'text' isn't a string must not crash
        # the join; only the well-formed string block contributes.
        data = {
            "final_request": {
                "max_tokens": 100,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": {"unexpected": "dict"}},
                            {"type": "text", "text": "real text"},
                        ],
                    }
                ],
            }
        }
        assert extract_preview(data) == "real text"


class TestUpdateSessionSummary:
    async def _row(self, pool: DatabasePool, session_id: str) -> dict:
        async with pool.connection() as conn:
            rows = await conn.fetch("SELECT * FROM session_summaries WHERE session_id = $1", session_id)
        return dict(rows[0])

    async def test_single_request_event(self, pool: DatabasePool) -> None:
        ts = datetime.now(UTC)
        async with pool.connection() as conn:
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="transaction.request_recorded",
                data=_request(),
                user_id="alice",
                timestamp=ts,
            )
        row = await self._row(pool, "s1")
        assert row["event_count"] == 1
        assert row["call_count"] == 1
        assert row["policy_event_count"] == 0
        assert row["user_id"] == "alice"
        assert row["models_used"] == "claude-x"
        assert row["preview_message"] == "hello"

    async def test_counts_accumulate(self, pool: DatabasePool) -> None:
        ts = datetime.now(UTC)
        async with pool.connection() as conn:
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="transaction.request_recorded",
                data=_request(model="m1", text="first"),
                user_id="alice",
                timestamp=ts,
            )
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="policy.block",
                data={},
                user_id=None,
                timestamp=ts + timedelta(seconds=1),
            )
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="transaction.request_recorded",
                data=_request(model="m2", text="second"),
                user_id=None,
                timestamp=ts + timedelta(seconds=2),
            )
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="policy.judge.evaluation",
                data={},
                user_id=None,
                timestamp=ts + timedelta(seconds=3),
            )
        row = await self._row(pool, "s1")
        assert row["event_count"] == 4
        assert row["call_count"] == 2
        assert row["policy_event_count"] == 1  # judge.evaluation excluded
        # models accumulate and dedupe; preview is set once (first)
        assert set(str(row["models_used"]).split(",")) == {"m1", "m2"}
        assert row["preview_message"] == "first"

    async def test_user_id_coalesced_not_overwritten(self, pool: DatabasePool) -> None:
        ts = datetime.now(UTC)
        async with pool.connection() as conn:
            # first event has no user_id
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="pipeline.client_request",
                data={},
                user_id=None,
                timestamp=ts,
            )
            # later event carries one — should fill it
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="transaction.request_recorded",
                data=_request(),
                user_id="bob",
                timestamp=ts + timedelta(seconds=1),
            )
            # a still-later event with a different user_id must NOT overwrite
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="policy.block",
                data={},
                user_id="carol",
                timestamp=ts + timedelta(seconds=2),
            )
        row = await self._row(pool, "s1")
        assert row["user_id"] == "bob"

    async def test_model_not_duplicated(self, pool: DatabasePool) -> None:
        ts = datetime.now(UTC)
        async with pool.connection() as conn:
            for i in range(3):
                await update_session_summary(
                    conn,
                    session_id="s1",
                    event_type="transaction.request_recorded",
                    data=_request(model="same"),
                    user_id=None,
                    timestamp=ts + timedelta(seconds=i),
                )
        row = await self._row(pool, "s1")
        assert row["models_used"] == "same"
        assert row["call_count"] == 3

    async def test_model_names_with_like_metacharacters_not_conflated(self, pool: DatabasePool) -> None:
        """A model containing LIKE wildcards ('%', '_') must not match a different
        model. Without ESCAPE, 'claude_x' would match 'claudeax' and drop it, and
        'm%' would match 'mZ'. All four are distinct and must all be retained."""
        ts = datetime.now(UTC)
        models = ["claude_x", "claudeax", "claude_x", "m%", "mZ", "m%"]
        async with pool.connection() as conn:
            for i, m in enumerate(models):
                await update_session_summary(
                    conn,
                    session_id="s1",
                    event_type="transaction.request_recorded",
                    data=_request(model=m),
                    user_id=None,
                    timestamp=ts + timedelta(seconds=i),
                )
        row = await self._row(pool, "s1")
        assert str(row["models_used"]).split(",") == ["claude_x", "claudeax", "m%", "mZ"]
