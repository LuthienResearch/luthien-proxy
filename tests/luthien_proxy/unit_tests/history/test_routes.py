"""Tests for history route handlers.

These tests focus on the HTTP layer - ensuring routes properly:
- Handle dependency injection
- Convert service exceptions to appropriate HTTP status codes
- Return correct response models
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException

from luthien_proxy.history.models import (
    ConversationMessage,
    ConversationTurn,
    MessageType,
    SessionDetail,
    SessionListResponse,
    SessionSearchParams,
    SessionSummary,
)
from luthien_proxy.history.routes import (
    api_router,
    export_session,
    get_session,
    list_sessions,
)

AUTH_TOKEN = "test-admin-key"


async def _collect_streaming_body(response) -> bytes:
    parts: list[bytes] = []
    async for chunk in response.body_iterator:
        parts.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(parts)


async def _single_chunk_stream(chunk: str | bytes):
    yield chunk


async def _raising_stream(error: Exception):
    raise error
    yield b""


class TestListSessionsRoute:
    """Test list_sessions route handler."""

    @pytest.mark.asyncio
    async def test_successful_list_sessions(self):
        """Test successful session list returns response."""
        mock_db_pool = MagicMock()
        expected_response = SessionListResponse(
            sessions=[
                SessionSummary(
                    session_id="session-1",
                    first_timestamp="2025-01-15T10:00:00",
                    last_timestamp="2025-01-15T11:00:00",
                    turn_count=3,
                    total_events=10,
                    policy_interventions=1,
                    models_used=["gpt-4"],
                ),
            ],
            total=100,
            offset=0,
            has_more=True,
        )

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=expected_response,
        ) as mock_fetch:
            result = await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user_id=None,
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=False,
            )

            assert isinstance(result, SessionListResponse)
            assert result.total == 100
            assert result.offset == 0
            assert result.has_more is True
            assert len(result.sessions) == 1
            assert result.sessions[0].session_id == "session-1"
            mock_fetch.assert_called_once_with(50, mock_db_pool, 0, user_id=None, search=SessionSearchParams())

    @pytest.mark.asyncio
    async def test_list_sessions_custom_limit(self):
        """Test session list respects limit parameter."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=SessionListResponse(sessions=[], total=0),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=100,
                offset=0,
                user_id=None,
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=False,
            )
            mock_fetch.assert_called_once_with(100, mock_db_pool, 0, user_id=None, search=SessionSearchParams())

    @pytest.mark.asyncio
    async def test_list_sessions_with_offset(self):
        """Test session list respects offset parameter for pagination."""
        mock_db_pool = MagicMock()
        expected_response = SessionListResponse(
            sessions=[],
            total=100,
            offset=50,
            has_more=True,
        )

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=expected_response,
        ) as mock_fetch:
            result = await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=50,
                user_id=None,
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=False,
            )

            assert result.offset == 50
            assert result.has_more is True
            mock_fetch.assert_called_once_with(50, mock_db_pool, 50, user_id=None, search=SessionSearchParams())

    @pytest.mark.asyncio
    async def test_list_sessions_forwards_search_filters(self):
        """Search query params are assembled into SessionSearchParams and forwarded."""
        from datetime import datetime

        mock_db_pool = MagicMock()
        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=SessionListResponse(sessions=[], total=0),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user_id="sami",
                model="claude-opus-4-6",
                from_time=datetime(2026, 4, 1),
                to_time=datetime(2026, 4, 12),
                q="error",
                policy_intervention=True,
            )
            mock_fetch.assert_called_once_with(
                50,
                mock_db_pool,
                0,
                user_id="sami",
                search=SessionSearchParams(
                    model="claude-opus-4-6",
                    from_time=datetime(2026, 4, 1),
                    to_time=datetime(2026, 4, 12),
                    q="error",
                    policy_intervention=True,
                ),
            )

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        """Test empty session list returns empty response."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=SessionListResponse(sessions=[], total=0),
        ):
            result = await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user_id=None,
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=False,
            )

            assert result.total == 0
            assert result.sessions == []
            assert result.has_more is False


class TestGetSessionRoute:
    """Test get_session route handler."""

    def test_openapi_documents_session_detail_schema(self):
        """Session detail streaming route keeps its documented response schema."""
        app = FastAPI()
        app.include_router(api_router)

        schema = app.openapi()["paths"]["/api/history/sessions/{session_id}"]["get"]["responses"]["200"]

        assert schema["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/SessionDetail"}

    @pytest.mark.asyncio
    async def test_successful_get_session(self):
        """Test successful session detail returns response."""
        mock_db_pool = MagicMock()
        expected_detail = SessionDetail(
            session_id="test-session",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2025-01-15T10:00:00",
                    model="gpt-4",
                    request_messages=[ConversationMessage(message_type=MessageType.USER, content="Hello")],
                    response_messages=[ConversationMessage(message_type=MessageType.ASSISTANT, content="Hi!")],
                    annotations=[],
                    had_policy_intervention=False,
                )
            ],
            total_policy_interventions=0,
            models_used=["gpt-4"],
            total_turns=1,
            offset=0,
            limit=50,
            has_more=False,
        )

        with patch(
            "luthien_proxy.history.routes.stream_session_detail_json",
            return_value=_single_chunk_stream(expected_detail.model_dump_json()),
        ) as mock_stream:
            result = await get_session(
                session_id="test-session", offset=10, limit=25, _=AUTH_TOKEN, db_pool=mock_db_pool
            )

            body = await _collect_streaming_body(result)
            assert json.loads(body) == expected_detail.model_dump(mode="json")
            mock_stream.assert_called_once_with("test-session", mock_db_pool, offset=10, limit=25)

    @pytest.mark.asyncio
    async def test_get_session_not_found(self):
        """Test 404 returned for non-existent session."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.stream_session_detail_json",
            return_value=_raising_stream(ValueError("No events found for session_id: nonexistent")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_session(session_id="nonexistent", offset=None, limit=50, _=AUTH_TOKEN, db_pool=mock_db_pool)

            assert exc_info.value.status_code == 404
            assert exc_info.value.detail == "Session not found."


class TestExportSessionRoute:
    """Test export_session route handler."""

    @pytest.mark.asyncio
    async def test_successful_export(self):
        """Test successful export returns markdown."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.stream_session_markdown",
            return_value=_single_chunk_stream("# Conversation History: test-session"),
        ):
            result = await export_session(session_id="test-session", _=AUTH_TOKEN, db_pool=mock_db_pool)

            assert result.media_type == "text/markdown"
            body = await _collect_streaming_body(result)
            assert "# Conversation History: test-session" in body.decode()
            assert "Content-Disposition" in result.headers
            assert 'filename="conversation_test-session.md"' in result.headers["Content-Disposition"]

    @pytest.mark.asyncio
    async def test_export_not_found(self):
        """Test 404 returned for non-existent session export."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.stream_session_markdown",
            return_value=_raising_stream(ValueError("No events found for session_id: nonexistent")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await export_session(session_id="nonexistent", _=AUTH_TOKEN, db_pool=mock_db_pool)

            assert exc_info.value.status_code == 404
            assert exc_info.value.detail == "Session not found."

    @pytest.mark.asyncio
    async def test_export_filename_sanitization(self):
        """Test that session IDs with special characters are sanitized in filename."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.stream_session_markdown",
            return_value=_single_chunk_stream(""),
        ):
            result = await export_session(
                session_id="test<script>alert(1)</script>",
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
            )

            # Filename should have special chars replaced with underscores
            disposition = result.headers["Content-Disposition"]
            assert "<" not in disposition
            assert ">" not in disposition
            assert "(" not in disposition
            assert ")" not in disposition


class TestUserLabelRoutes:
    """User label endpoints, exercised against a real in-memory SQLite pool."""

    @pytest.fixture
    async def pool(self):
        from luthien_proxy.utils.db import DatabasePool
        from luthien_proxy.utils.migration_check import check_migrations

        p = DatabasePool("sqlite://:memory:")
        await check_migrations(p)
        return p

    @pytest.mark.asyncio
    async def test_set_then_list_then_delete(self, pool):
        from luthien_proxy.history.routes import (
            UserLabelRequest,
            delete_user_label,
            list_user_labels,
            set_user_label,
        )

        set_result = await set_user_label(
            user_id="alice", body=UserLabelRequest(display_name="Alice"), _=AUTH_TOKEN, db_pool=pool
        )
        assert set_result == {"user_id": "alice", "display_name": "Alice"}

        listed = await list_user_labels(_=AUTH_TOKEN, db_pool=pool)
        assert listed == {"labels": {"alice": "Alice"}}

        deleted = await delete_user_label(user_id="alice", _=AUTH_TOKEN, db_pool=pool)
        assert deleted == {"deleted": True}
        assert await list_user_labels(_=AUTH_TOKEN, db_pool=pool) == {"labels": {}}

    @pytest.mark.asyncio
    async def test_set_blank_returns_400(self, pool):
        from luthien_proxy.history.routes import UserLabelRequest, set_user_label

        with pytest.raises(HTTPException) as exc:
            await set_user_label(user_id="alice", body=UserLabelRequest(display_name="   "), _=AUTH_TOKEN, db_pool=pool)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_list_users(self, pool):
        from datetime import UTC, datetime

        from luthien_proxy.history.routes import list_users
        from luthien_proxy.observability.session_summary import update_session_summary

        async with pool.connection() as conn:
            await update_session_summary(
                conn,
                session_id="s1",
                event_type="transaction.request_recorded",
                data={"final_model": "m", "final_request": {"messages": [{"role": "user", "content": "hi"}]}},
                user_id="bob",
                timestamp=datetime.now(UTC),
            )
        result = await list_users(_=AUTH_TOKEN, db_pool=pool, limit=500, offset=0)
        assert result == {"users": ["bob"], "labels": {}}
