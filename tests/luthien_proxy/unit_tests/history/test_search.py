"""Tests for server-side session search functionality.

Tests cover:
- SessionSearchParams model validation
- Route handler search query parameter wiring
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.history.models import (
    SessionListResponse,
    SessionSearchParams,
    SessionSummary,
)
from luthien_proxy.history.routes import list_sessions

AUTH_TOKEN = "test-admin-key"


# ---------------------------------------------------------------------------
# SessionSearchParams model tests
# ---------------------------------------------------------------------------


class TestSessionSearchParams:
    """Test SessionSearchParams model validation."""

    def test_default_empty(self):
        """All fields default to None / False."""
        params = SessionSearchParams()
        assert params.user is None
        assert params.model is None
        assert params.from_time is None
        assert params.to_time is None
        assert params.q is None
        assert params.policy_intervention is None

    def test_all_fields_set(self):
        """All fields can be set."""
        from_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 4, 1, tzinfo=timezone.utc)
        params = SessionSearchParams(
            user="sami",
            model="claude-opus-4-6",
            from_time=from_dt,
            to_time=to_dt,
            q="error handling",
            policy_intervention=True,
        )
        assert params.user == "sami"
        assert params.model == "claude-opus-4-6"
        assert params.from_time == from_dt
        assert params.to_time == to_dt
        assert params.q == "error handling"
        assert params.policy_intervention is True

    def test_is_empty_when_all_none(self):
        """is_empty returns True when no filters set."""
        params = SessionSearchParams()
        assert params.is_empty() is True

    def test_is_empty_false_when_user_set(self):
        """is_empty returns False when user is set."""
        params = SessionSearchParams(user="sami")
        assert params.is_empty() is False

    def test_is_empty_false_when_q_set(self):
        """is_empty returns False when q is set."""
        params = SessionSearchParams(q="error")
        assert params.is_empty() is False

    def test_is_empty_false_when_policy_intervention_true(self):
        """is_empty returns False when policy_intervention is True."""
        params = SessionSearchParams(policy_intervention=True)
        assert params.is_empty() is False

    def test_is_empty_true_when_policy_intervention_false(self):
        """is_empty returns True when policy_intervention is explicitly False (no filter)."""
        params = SessionSearchParams(policy_intervention=False)
        assert params.is_empty() is True


# Service-layer SQL behavior is covered by:
#   - tests/luthien_proxy/unit_tests/history/test_service_sqlite.py
#   - tests/luthien_proxy/unit_tests/history/test_search_bugs.py


# ---------------------------------------------------------------------------
# Route handler tests — search query params wired correctly
# ---------------------------------------------------------------------------


class TestListSessionsRouteSearchParams:
    """Test list_sessions route handler passes search params to service."""

    def _make_empty_response(self) -> SessionListResponse:
        return SessionListResponse(sessions=[], total=0, offset=0, has_more=False)

    @pytest.mark.asyncio
    async def test_list_sessions_no_search_params(self):
        """list_sessions with no search params calls fetch_session_list with empty search."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=self._make_empty_response(),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user=None,
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=None,
            )
            call_args = mock_fetch.call_args[0]
            search = call_args[3]
            assert isinstance(search, SessionSearchParams)
            assert search.is_empty()

    @pytest.mark.asyncio
    async def test_list_sessions_user_filter(self):
        """list_sessions passes user filter to service."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=self._make_empty_response(),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user="sami",
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=None,
            )
            search = mock_fetch.call_args[0][3]
            assert search.user == "sami"

    @pytest.mark.asyncio
    async def test_list_sessions_model_filter(self):
        """list_sessions passes model filter to service."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=self._make_empty_response(),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user=None,
                model="claude-opus-4-6",
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=None,
            )
            search = mock_fetch.call_args[0][3]
            assert search.model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_list_sessions_time_range_filter(self):
        """list_sessions passes time range to service."""
        mock_db_pool = MagicMock()
        from_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 4, 1, tzinfo=timezone.utc)

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=self._make_empty_response(),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user=None,
                model=None,
                from_time=from_dt,
                to_time=to_dt,
                q=None,
                policy_intervention=None,
            )
            search = mock_fetch.call_args[0][3]
            assert search.from_time == from_dt
            assert search.to_time == to_dt

    @pytest.mark.asyncio
    async def test_list_sessions_q_filter(self):
        """list_sessions passes full-text query to service."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=self._make_empty_response(),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user=None,
                model=None,
                from_time=None,
                to_time=None,
                q="error handling",
                policy_intervention=None,
            )
            search = mock_fetch.call_args[0][3]
            assert search.q == "error handling"

    @pytest.mark.asyncio
    async def test_list_sessions_policy_intervention_filter(self):
        """list_sessions passes policy_intervention filter to service."""
        mock_db_pool = MagicMock()

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=self._make_empty_response(),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user=None,
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=True,
            )
            search = mock_fetch.call_args[0][3]
            assert search.policy_intervention is True

    @pytest.mark.asyncio
    async def test_list_sessions_all_search_params(self):
        """list_sessions passes all search params together."""
        mock_db_pool = MagicMock()
        from_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 4, 1, tzinfo=timezone.utc)

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=self._make_empty_response(),
        ) as mock_fetch:
            await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=25,
                offset=10,
                user="sami",
                model="claude-opus-4-6",
                from_time=from_dt,
                to_time=to_dt,
                q="error",
                policy_intervention=True,
            )
            call_args = mock_fetch.call_args[0]
            assert call_args[0] == 25  # limit
            assert call_args[2] == 10  # offset
            search = call_args[3]
            assert search.user == "sami"
            assert search.model == "claude-opus-4-6"
            assert search.from_time == from_dt
            assert search.to_time == to_dt
            assert search.q == "error"
            assert search.policy_intervention is True

    @pytest.mark.asyncio
    async def test_list_sessions_backward_compatible(self):
        """list_sessions without new params still works (backward compat)."""
        mock_db_pool = MagicMock()
        expected = SessionListResponse(
            sessions=[
                SessionSummary(
                    session_id="session-1",
                    first_timestamp="2026-01-01T00:00:00",
                    last_timestamp="2026-01-01T01:00:00",
                    turn_count=2,
                    total_events=5,
                    policy_interventions=0,
                    models_used=["claude-opus-4-6"],
                )
            ],
            total=1,
            offset=0,
            has_more=False,
        )

        with patch(
            "luthien_proxy.history.routes.fetch_session_list",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_fetch:
            result = await list_sessions(
                _=AUTH_TOKEN,
                db_pool=mock_db_pool,
                limit=50,
                offset=0,
                user=None,
                model=None,
                from_time=None,
                to_time=None,
                q=None,
                policy_intervention=None,
            )
            assert result.total == 1
            assert len(result.sessions) == 1
            assert result.sessions[0].session_id == "session-1"
            mock_fetch.assert_called_once()
