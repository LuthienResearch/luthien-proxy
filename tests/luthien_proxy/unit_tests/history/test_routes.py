"""Tests for history route handlers.

These tests focus on the HTTP layer - ensuring routes properly:
- Handle dependency injection
- Convert service exceptions to appropriate HTTP status codes
- Return correct response models
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from luthien_proxy.history.models import (
    ConversationMessage,
    ConversationTurn,
    MessageType,
    SessionDetail,
    SessionListResponse,
    SessionSummary,
)
from luthien_proxy.history.routes import (
    export_session,
    get_session,
    list_sessions,
)

AUTH_TOKEN = "test-admin-key"


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
            result = await list_sessions(_=AUTH_TOKEN, db_pool=mock_db_pool, limit=50, offset=0)

            assert isinstance(result, SessionListResponse)
            assert result.total == 100
            assert result.offset == 0
            assert result.has_more is True
            assert len(result.sessions) == 1
            assert result.sessions[0].session_id == "session-1"
            mock_fetch.assert_called_once_with(50, mock_db_pool, 0)

    @pytest.mark.asyncio
    async def test_list_sessions_custom_limit(self):
        """Test session list respects limit parameter."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=SessionListResponse(sessions=[], total=0),
        ) as mock_fetch:
            await list_sessions(_=AUTH_TOKEN, db_pool=mock_db_pool, limit=100, offset=0)
            mock_fetch.assert_called_once_with(100, mock_db_pool, 0)

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
            result = await list_sessions(_=AUTH_TOKEN, db_pool=mock_db_pool, limit=50, offset=50)

            assert result.offset == 50
            assert result.has_more is True
            mock_fetch.assert_called_once_with(50, mock_db_pool, 50)

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        """Test empty session list returns empty response."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=SessionListResponse(sessions=[], total=0),
        ):
            result = await list_sessions(_=AUTH_TOKEN, db_pool=mock_db_pool, limit=50, offset=0)

            assert result.total == 0
            assert result.sessions == []
            assert result.has_more is False


class TestGetSessionRoute:
    """Test get_session route handler."""

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
        )

        with patch(
            "luthien_proxy.history.routes.fetch_session_detail",
            new_callable=AsyncMock,
            return_value=expected_detail,
        ) as mock_fetch:
            result = await get_session(session_id="test-session", _=AUTH_TOKEN, db_pool=mock_db_pool)

            assert isinstance(result, SessionDetail)
            assert result.session_id == "test-session"
            assert len(result.turns) == 1
            mock_fetch.assert_called_once_with("test-session", mock_db_pool)

    @pytest.mark.asyncio
    async def test_get_session_not_found(self):
        """Test 404 returned for non-existent session."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_detail",
            new_callable=AsyncMock,
            side_effect=ValueError("No events found for session_id: nonexistent"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_session(session_id="nonexistent", _=AUTH_TOKEN, db_pool=mock_db_pool)

            assert exc_info.value.status_code == 404
            assert "No events found" in exc_info.value.detail


class TestExportSessionRoute:
    """Test export_session route handler."""

    @pytest.mark.asyncio
    async def test_successful_export(self):
        """Test successful export returns markdown."""
        mock_db_pool = MagicMock()
        session_detail = SessionDetail(
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
        )

        with patch(
            "luthien_proxy.history.routes.fetch_session_detail",
            new_callable=AsyncMock,
            return_value=session_detail,
        ):
            result = await export_session(session_id="test-session", _=AUTH_TOKEN, db_pool=mock_db_pool)

            assert result.media_type == "text/markdown"
            assert "# Conversation History: test-session" in result.body.decode()
            assert "Content-Disposition" in result.headers
            assert 'filename="conversation_test-session.md"' in result.headers["Content-Disposition"]

    @pytest.mark.asyncio
    async def test_export_not_found(self):
        """Test 404 returned for non-existent session export."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_detail",
            new_callable=AsyncMock,
            side_effect=ValueError("No events found for session_id: nonexistent"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await export_session(session_id="nonexistent", _=AUTH_TOKEN, db_pool=mock_db_pool)

            assert exc_info.value.status_code == 404
            assert "No events found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_export_filename_sanitization(self):
        """Test that session IDs with special characters are sanitized in filename."""
        mock_db_pool = MagicMock()
        session_detail = SessionDetail(
            session_id="test<script>alert(1)</script>",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[],
            total_policy_interventions=0,
            models_used=[],
        )

        with patch(
            "luthien_proxy.history.routes.fetch_session_detail",
            new_callable=AsyncMock,
            return_value=session_detail,
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
