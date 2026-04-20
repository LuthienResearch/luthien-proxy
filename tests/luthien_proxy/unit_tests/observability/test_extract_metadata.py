"""Tests for _extract_session_metadata in emitter.py.

This function extracts model names and a preview message from batched
DB queue items for session_summaries. Key behaviors:
- Extracts model from data["final_model"]
- Extracts preview from earliest non-probe request's user messages
- Skips probe requests (max_tokens <= 1)
- Handles Anthropic content block format (list of dicts with type/text)
- Strips <system-reminder> tags
- Truncates preview to 200 chars
- Deduplicates models
"""

from datetime import UTC, datetime

from luthien_proxy.observability.emitter import DbQueueItem, _extract_session_metadata


def _make_event(
    event_type: str = "transaction.request_recorded",
    data: dict | None = None,
    timestamp: datetime | None = None,
    transaction_id: str = "tx-1",
) -> DbQueueItem:
    """Helper to build a DbQueueItem tuple for testing."""
    return (
        transaction_id,
        event_type,
        data or {},
        timestamp or datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
        "session-1",  # session_id
        None,  # user_hash
    )


class TestExtractSessionMetadata:
    """Tests for _extract_session_metadata."""

    def test_empty_events_returns_empty(self):
        models, preview = _extract_session_metadata([])
        assert models == []
        assert preview is None

    def test_extracts_model_from_final_model(self):
        event = _make_event(data={"final_model": "claude-sonnet-4-20250514"})
        models, _ = _extract_session_metadata([event])
        assert models == ["claude-sonnet-4-20250514"]

    def test_deduplicates_models(self):
        events = [
            _make_event(
                data={"final_model": "claude-sonnet-4-20250514"},
                transaction_id="tx-1",
                timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            ),
            _make_event(
                data={"final_model": "claude-sonnet-4-20250514"},
                transaction_id="tx-2",
                timestamp=datetime(2025, 1, 15, 10, 1, 0, tzinfo=UTC),
            ),
            _make_event(
                data={"final_model": "claude-opus-4-20250514"},
                transaction_id="tx-3",
                timestamp=datetime(2025, 1, 15, 10, 2, 0, tzinfo=UTC),
            ),
        ]
        models, _ = _extract_session_metadata(events)
        assert models == ["claude-sonnet-4-20250514", "claude-opus-4-20250514"]

    def test_extracts_preview_from_user_message_string(self):
        event = _make_event(
            data={
                "final_model": "claude-sonnet-4-20250514",
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": "Hello, how are you?"}],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "Hello, how are you?"

    def test_extracts_preview_from_anthropic_content_blocks(self):
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "First block."},
                                {"type": "text", "text": "Second block."},
                            ],
                        }
                    ],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "First block. Second block."

    def test_skips_probe_requests(self):
        """Probe requests (max_tokens <= 1) should be skipped for preview."""
        events = [
            _make_event(
                data={
                    "final_model": "claude-sonnet-4-20250514",
                    "final_request": {
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "probe message"}],
                    },
                },
                transaction_id="tx-probe",
                timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            ),
            _make_event(
                data={
                    "final_model": "claude-sonnet-4-20250514",
                    "final_request": {
                        "max_tokens": 4096,
                        "messages": [{"role": "user", "content": "real message"}],
                    },
                },
                transaction_id="tx-real",
                timestamp=datetime(2025, 1, 15, 10, 1, 0, tzinfo=UTC),
            ),
        ]
        _, preview = _extract_session_metadata(events)
        assert preview == "real message"

    def test_probe_with_zero_max_tokens_skipped(self):
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 0,
                    "messages": [{"role": "user", "content": "should be skipped"}],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview is None

    def test_strips_system_reminder_tags(self):
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": "<system-reminder>secret stuff</system-reminder>Actual question here",
                        }
                    ],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "Actual question here"

    def test_strips_system_reminder_multiline(self):
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": "<system-reminder>\nline1\nline2\n</system-reminder>User text",
                        }
                    ],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "User text"

    def test_truncates_preview_to_200_chars(self):
        long_text = "x" * 300
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": long_text}],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview is not None
        assert len(preview) == 203  # 200 + "..."
        assert preview.endswith("...")

    def test_uses_earliest_non_probe_for_preview(self):
        """Preview should come from the earliest (by timestamp) non-probe event."""
        events = [
            _make_event(
                data={
                    "final_request": {
                        "max_tokens": 4096,
                        "messages": [{"role": "user", "content": "later message"}],
                    },
                },
                transaction_id="tx-2",
                timestamp=datetime(2025, 1, 15, 11, 0, 0, tzinfo=UTC),
            ),
            _make_event(
                data={
                    "final_request": {
                        "max_tokens": 4096,
                        "messages": [{"role": "user", "content": "earlier message"}],
                    },
                },
                transaction_id="tx-1",
                timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            ),
        ]
        _, preview = _extract_session_metadata(events)
        assert preview == "earlier message"

    def test_ignores_non_request_events(self):
        """Events that aren't transaction.request_recorded should be ignored."""
        events = [
            _make_event(
                event_type="policy.modified_request",
                data={"final_model": "claude-sonnet-4-20250514"},
            ),
            _make_event(
                event_type="transaction.response_recorded",
                data={"final_model": "claude-opus-4-20250514"},
            ),
        ]
        models, preview = _extract_session_metadata(events)
        assert models == []
        assert preview is None

    def test_skips_assistant_messages(self):
        """Only user messages should be used for preview."""
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [
                        {"role": "assistant", "content": "I am helpful"},
                        {"role": "user", "content": "User question"},
                    ],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "User question"

    def test_falls_back_to_original_request(self):
        """When final_request is missing, falls back to original_request."""
        event = _make_event(
            data={
                "original_request": {
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": "from original"}],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "from original"

    def test_skips_empty_user_messages(self):
        """Whitespace-only user messages should be skipped."""
        events = [
            _make_event(
                data={
                    "final_request": {
                        "max_tokens": 4096,
                        "messages": [
                            {"role": "user", "content": "   "},
                            {"role": "user", "content": "Real content"},
                        ],
                    },
                },
            ),
        ]
        _, preview = _extract_session_metadata(events)
        assert preview == "Real content"

    def test_whitespace_normalized_in_preview(self):
        """Multi-line / multi-space text should be normalized to single spaces."""
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": "hello\n\n  world\tfoo"}],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "hello world foo"

    def test_content_block_with_non_text_types_ignored(self):
        """Non-text content blocks (e.g. image) should be ignored."""
        event = _make_event(
            data={
                "final_request": {
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "source": {"data": "..."}},
                                {"type": "text", "text": "Describe this image"},
                            ],
                        }
                    ],
                },
            }
        )
        _, preview = _extract_session_metadata([event])
        assert preview == "Describe this image"

    def test_no_model_when_final_model_missing(self):
        """Events without final_model should not contribute models."""
        event = _make_event(data={"some_other_field": "value"})
        models, _ = _extract_session_metadata([event])
        assert models == []

    def test_message_only_system_reminder_skipped(self):
        """A message that is entirely a system-reminder tag should be skipped."""
        events = [
            _make_event(
                data={
                    "final_request": {
                        "max_tokens": 4096,
                        "messages": [
                            {"role": "user", "content": "<system-reminder>only reminder</system-reminder>"},
                            {"role": "user", "content": "actual question"},
                        ],
                    },
                },
            ),
        ]
        _, preview = _extract_session_metadata(events)
        assert preview == "actual question"
