"""Tests for conversation history service layer.

Tests the pure business logic functions for fetching sessions,
parsing conversation turns, and exporting to markdown.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.history.models import (
    ConversationMessage,
    ConversationTurn,
    MessageType,
    PolicyAnnotation,
    SessionDetail,
)
from luthien_proxy.history.service import (
    _build_turn,
    _extract_first_user_message,
    _extract_text_content,
    _extract_tool_calls,
    _parse_request_messages,
    _parse_response_messages,
    _safe_parse_json,
    export_session_markdown,
    fetch_session_detail,
    fetch_session_list,
)


class TestExtractTextContent:
    """Test text content extraction from various message formats."""

    @pytest.mark.parametrize(
        "content,expected",
        [
            ("Hello world", "Hello world"),
            ("", ""),
            (None, ""),
            ([{"type": "text", "text": "First"}], "First"),
            ([{"type": "text", "text": "A"}, {"type": "text", "text": "B"}], "A\nB"),
            ([{"type": "image", "url": "http://..."}], ""),
            ([{"type": "text", "text": "Text"}, {"type": "tool_use", "id": "123"}], "Text"),
        ],
    )
    def test_extract_content(self, content, expected):
        """Test extracting content from various formats."""
        assert _extract_text_content(content) == expected


class TestExtractFirstUserMessage:
    """Test first user message extraction for session previews."""

    def test_basic_message(self):
        """Test extracting a basic user message."""
        payload = {"final_request": {"messages": [{"role": "user", "content": "Hello world"}]}}
        assert _extract_first_user_message(payload) == "Hello world"

    def test_multiple_messages_returns_last_user(self):
        """Test that the last user message is returned (most recent context)."""
        payload = {
            "final_request": {
                "messages": [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "First question"},
                    {"role": "assistant", "content": "Answer"},
                    {"role": "user", "content": "Follow-up question"},
                ]
            }
        }
        assert _extract_first_user_message(payload) == "Follow-up question"

    def test_truncates_long_messages(self):
        """Test that long messages are truncated to 100 chars."""
        long_message = "x" * 150
        payload = {"final_request": {"messages": [{"role": "user", "content": long_message}]}}
        result = _extract_first_user_message(payload)
        assert len(result) == 103  # 100 chars + "..."
        assert result.endswith("...")

    def test_normalizes_whitespace(self):
        """Test that newlines and extra whitespace are collapsed."""
        payload = {"final_request": {"messages": [{"role": "user", "content": "Hello\n\nworld\n  test"}]}}
        assert _extract_first_user_message(payload) == "Hello world test"

    def test_none_payload(self):
        """Test handling of None payload."""
        assert _extract_first_user_message(None) is None

    def test_empty_payload(self):
        """Test handling of empty dict payload."""
        assert _extract_first_user_message({}) is None

    def test_no_user_messages(self):
        """Test handling when no user messages present."""
        payload = {"final_request": {"messages": [{"role": "system", "content": "System prompt"}]}}
        assert _extract_first_user_message(payload) is None

    def test_json_string_payload(self):
        """Test handling of JSON string payload from asyncpg."""
        import json

        payload_dict = {"final_request": {"messages": [{"role": "user", "content": "From JSON"}]}}
        payload_str = json.dumps(payload_dict)
        assert _extract_first_user_message(payload_str) == "From JSON"

    def test_falls_back_to_original_request(self):
        """Test fallback to original_request when final_request missing."""
        payload = {"original_request": {"messages": [{"role": "user", "content": "Fallback message"}]}}
        assert _extract_first_user_message(payload) == "Fallback message"


class TestSafeParseJson:
    """Test safe JSON parsing."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ('{"key": "value"}', {"key": "value"}),
            ("{}", {}),
            ('{"nested": {"a": 1}}', {"nested": {"a": 1}}),
            ("invalid", None),
            ("[]", None),  # Not a dict
            ('"string"', None),  # Not a dict
        ],
    )
    def test_parse_json(self, input_str, expected):
        """Test JSON parsing with various inputs."""
        assert _safe_parse_json(input_str) == expected


class TestExtractToolCalls:
    """Test tool call extraction from messages."""

    def test_openai_style_tool_calls(self):
        """Test extracting OpenAI-style tool calls."""
        message = {
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'},
                }
            ]
        }

        result = _extract_tool_calls(message)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_CALL
        assert result[0].tool_name == "read_file"
        assert result[0].tool_call_id == "call_123"
        assert result[0].tool_input == {"path": "/tmp/test"}

    def test_anthropic_style_content_blocks(self):
        """Test extracting Anthropic-style tool_use content blocks."""
        message = {
            "content": [
                {"type": "text", "text": "Let me read that file"},
                {"type": "tool_use", "id": "toolu_123", "name": "Read", "input": {"file": "test.py"}},
            ]
        }

        result = _extract_tool_calls(message)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_CALL
        assert result[0].tool_name == "Read"
        assert result[0].tool_call_id == "toolu_123"
        assert result[0].tool_input == {"file": "test.py"}

    def test_no_tool_calls(self):
        """Test message without tool calls."""
        message = {"content": "Hello world"}
        result = _extract_tool_calls(message)
        assert len(result) == 0

    def test_explicit_none_tool_calls(self):
        """Test message with tool_calls explicitly set to None.

        This case occurs in real OpenAI responses where tool_calls is present
        but null, not just missing from the dict.
        """
        message = {"content": "Hello world", "tool_calls": None}
        result = _extract_tool_calls(message)
        assert len(result) == 0


class TestParseRequestMessages:
    """Test request message parsing."""

    def test_simple_messages(self):
        """Test parsing simple text messages."""
        request = {
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 2
        assert result[0].message_type == MessageType.SYSTEM
        assert result[0].content == "You are helpful"
        assert result[1].message_type == MessageType.USER
        assert result[1].content == "Hello"

    def test_assistant_message_with_tool_calls(self):
        """Test parsing assistant messages with tool_calls in request.

        When conversation history includes an assistant message that made
        tool calls, those tool calls must be extracted and included.
        """
        request = {
            "messages": [
                {"role": "user", "content": "What's the weather in Tokyo?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"location": "Tokyo"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_abc123",
                    "content": '{"temperature": 22, "conditions": "sunny"}',
                },
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 3
        # First: user message
        assert result[0].message_type == MessageType.USER
        assert "weather" in result[0].content.lower()
        # Second: tool call from assistant
        assert result[1].message_type == MessageType.TOOL_CALL
        assert result[1].tool_name == "get_weather"
        assert result[1].tool_call_id == "call_abc123"
        # Third: tool result
        assert result[2].message_type == MessageType.TOOL_RESULT
        assert result[2].tool_call_id == "call_abc123"

    def test_tool_result_message(self):
        """Test parsing tool result messages."""
        request = {
            "messages": [
                {"role": "tool", "content": "File contents...", "tool_call_id": "call_123"},
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].tool_call_id == "call_123"


class TestParseResponseMessages:
    """Test response message parsing."""

    def test_simple_response(self):
        """Test parsing simple text response."""
        response = {"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]}

        result = _parse_response_messages(response)

        assert len(result) == 1
        assert result[0].message_type == MessageType.ASSISTANT
        assert result[0].content == "Hello!"

    def test_response_with_tool_calls(self):
        """Test parsing response with tool calls."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Let me check",
                        "tool_calls": [{"id": "call_1", "function": {"name": "read", "arguments": "{}"}}],
                    }
                }
            ]
        }

        result = _parse_response_messages(response)

        assert len(result) == 2
        assert result[0].message_type == MessageType.ASSISTANT
        assert result[1].message_type == MessageType.TOOL_CALL


class TestBuildTurn:
    """Test building conversation turns from events."""

    def test_simple_turn(self):
        """Test building a simple request/response turn."""
        events = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "final_request": {"messages": [{"role": "user", "content": "Hello"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "event_type": "transaction.streaming_response_recorded",
                "payload": {
                    "original_response": {"choices": [{"message": {"content": "Hi!"}}]},
                    "final_response": {"choices": [{"message": {"content": "Hi!"}}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 1),
            },
        ]

        turn = _build_turn("call-123", events)

        assert turn.call_id == "call-123"
        assert turn.model == "gpt-4"
        assert len(turn.request_messages) == 1
        assert len(turn.response_messages) == 1
        assert not turn.had_policy_intervention

    def test_turn_with_policy_intervention(self):
        """Test turn with policy modification."""
        events = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Original"}]},
                    "final_request": {"messages": [{"role": "user", "content": "Modified"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "event_type": "policy.judge.tool_call_blocked",
                "payload": {"summary": "Tool call blocked for safety"},
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
        ]

        turn = _build_turn("call-123", events)

        assert turn.had_policy_intervention
        assert turn.request_messages[0].was_modified
        assert turn.request_messages[0].original_content == "Original"
        assert len(turn.annotations) == 1
        assert turn.annotations[0].policy_name == "judge"


class TestFetchSessionList:
    """Test fetching session list from database."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Test successful session list fetching."""
        mock_rows = [
            {
                "session_id": "session-1",
                "first_ts": datetime(2025, 1, 15, 10, 0, 0),
                "last_ts": datetime(2025, 1, 15, 11, 0, 0),
                "total_events": 10,
                "turn_count": 3,
                "policy_interventions": 1,
                "models": ["gpt-4", "claude-3"],
                "request_payload": {"final_request": {"messages": [{"role": "user", "content": "Hello world"}]}},
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1  # Total count
        mock_conn.fetch.return_value = mock_rows

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool)

        assert result.total == 1
        assert result.offset == 0
        assert result.has_more is False
        assert len(result.sessions) == 1
        assert result.sessions[0].session_id == "session-1"
        assert result.sessions[0].turn_count == 3
        assert result.sessions[0].policy_interventions == 1
        assert "gpt-4" in result.sessions[0].models_used
        assert result.sessions[0].first_user_message == "Hello world"

    @pytest.mark.asyncio
    async def test_fetch_with_offset(self):
        """Test fetching with offset for pagination."""
        mock_rows = [
            {
                "session_id": "session-2",
                "first_ts": datetime(2025, 1, 15, 10, 0, 0),
                "last_ts": datetime(2025, 1, 15, 11, 0, 0),
                "total_events": 5,
                "turn_count": 2,
                "policy_interventions": 0,
                "models": ["gpt-4"],
                "request_payload": None,  # Test with no first message
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 100  # Total count
        mock_conn.fetch.return_value = mock_rows

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool, offset=50)

        assert result.total == 100
        assert result.offset == 50
        assert result.has_more is True  # 50 + 1 < 100
        assert len(result.sessions) == 1
        assert result.sessions[0].first_user_message is None

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """Test when no sessions found."""
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 0  # Total count
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool)

        assert result.total == 0
        assert result.offset == 0
        assert result.has_more is False
        assert result.sessions == []


class TestFetchSessionDetail:
    """Test fetching session detail from database."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Test successful session detail fetching."""
        mock_rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Hi"}]},
                    "final_request": {"messages": [{"role": "user", "content": "Hi"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "call_id": "call-1",
                "event_type": "transaction.streaming_response_recorded",
                "payload": {
                    "original_response": {"choices": [{"message": {"content": "Hello!"}}]},
                    "final_response": {"choices": [{"message": {"content": "Hello!"}}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 1),
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = mock_rows

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_detail("session-1", mock_pool)

        assert result.session_id == "session-1"
        assert len(result.turns) == 1
        assert result.turns[0].model == "gpt-4"

    @pytest.mark.asyncio
    async def test_no_events_found(self):
        """Test error when no events found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(ValueError, match="No events found"):
            await fetch_session_detail("nonexistent", mock_pool)

    @pytest.mark.asyncio
    async def test_payload_as_json_string(self):
        """Test handling of JSONB payload returned as string by asyncpg.

        asyncpg returns JSONB columns as strings, not dicts. The service
        must parse these strings into dicts for proper processing.
        """
        import json

        payload_dict = {
            "final_model": "gpt-4",
            "original_request": {"messages": [{"role": "user", "content": "Hi"}]},
            "final_request": {"messages": [{"role": "user", "content": "Hi"}]},
        }
        response_payload = {
            "original_response": {"choices": [{"message": {"content": "Hello!"}}]},
            "final_response": {"choices": [{"message": {"content": "Hello!"}}]},
        }

        mock_rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": json.dumps(payload_dict),  # String, not dict
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "call_id": "call-1",
                "event_type": "transaction.streaming_response_recorded",
                "payload": json.dumps(response_payload),  # String, not dict
                "created_at": datetime(2025, 1, 15, 10, 0, 1),
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = mock_rows

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_detail("session-1", mock_pool)

        assert result.session_id == "session-1"
        assert len(result.turns) == 1
        assert result.turns[0].model == "gpt-4"
        assert len(result.turns[0].request_messages) == 1
        assert result.turns[0].request_messages[0].content == "Hi"
        assert len(result.turns[0].response_messages) == 1
        assert result.turns[0].response_messages[0].content == "Hello!"


class TestExportSessionMarkdown:
    """Test markdown export functionality."""

    def test_basic_export(self):
        """Test basic markdown export."""
        session = SessionDetail(
            session_id="test-session",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2025-01-15T10:00:00",
                    model="gpt-4",
                    request_messages=[ConversationMessage(message_type=MessageType.USER, content="Hello")],
                    response_messages=[ConversationMessage(message_type=MessageType.ASSISTANT, content="Hi there!")],
                    annotations=[],
                    had_policy_intervention=False,
                )
            ],
            total_policy_interventions=0,
            models_used=["gpt-4"],
        )

        markdown = export_session_markdown(session)

        assert "# Conversation History: test-session" in markdown
        assert "## Turn 1" in markdown
        assert "### User" in markdown
        assert "Hello" in markdown
        assert "### Assistant" in markdown
        assert "Hi there!" in markdown

    def test_export_with_tool_call(self):
        """Test markdown export with tool calls."""
        session = SessionDetail(
            session_id="test-session",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2025-01-15T10:00:00",
                    model="gpt-4",
                    request_messages=[],
                    response_messages=[
                        ConversationMessage(
                            message_type=MessageType.TOOL_CALL,
                            content="{}",
                            tool_name="read_file",
                            tool_input={"path": "/tmp/test"},
                        )
                    ],
                    annotations=[],
                    had_policy_intervention=False,
                )
            ],
            total_policy_interventions=0,
            models_used=["gpt-4"],
        )

        markdown = export_session_markdown(session)

        assert "### Tool Call" in markdown
        assert "`read_file`" in markdown
        assert '"/tmp/test"' in markdown

    def test_export_with_policy_annotations(self):
        """Test markdown export with policy annotations."""
        session = SessionDetail(
            session_id="test-session",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2025-01-15T10:00:00",
                    model="gpt-4",
                    request_messages=[],
                    response_messages=[],
                    annotations=[
                        PolicyAnnotation(
                            policy_name="judge",
                            event_type="policy.judge.tool_call_blocked",
                            summary="Dangerous operation blocked",
                        )
                    ],
                    had_policy_intervention=True,
                )
            ],
            total_policy_interventions=1,
            models_used=["gpt-4"],
        )

        markdown = export_session_markdown(session)

        assert "### Policy Annotations" in markdown
        assert "**judge**" in markdown
        assert "Dangerous operation blocked" in markdown
        assert "**Policy Interventions:** 1" in markdown
