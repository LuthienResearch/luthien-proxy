"""Tests for PolicyContext span and event helpers."""

from unittest.mock import MagicMock, patch

from opentelemetry import trace

from luthien_proxy.policy_core.policy_context import PolicyContext


class TestPolicyContextSpan:
    """Tests for PolicyContext.span() method."""

    def test_span_creates_child_span_with_policy_prefix(self):
        """Span names are prefixed with 'policy.' automatically."""
        ctx = PolicyContext.for_testing(transaction_id="test-123")

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=None)

        with patch("luthien_proxy.policy_core.policy_context._tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value = mock_span

            with ctx.span("check_safety") as span:
                assert span is mock_span

            mock_tracer.start_as_current_span.assert_called_once_with("policy.check_safety")

    def test_span_does_not_double_prefix(self):
        """If name already has policy. prefix, don't add it again."""
        ctx = PolicyContext.for_testing(transaction_id="test-123")

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=None)

        with patch("luthien_proxy.policy_core.policy_context._tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value = mock_span

            with ctx.span("policy.already_prefixed"):
                pass

            mock_tracer.start_as_current_span.assert_called_once_with("policy.already_prefixed")

    def test_span_includes_transaction_id(self):
        """Spans include the transaction_id attribute."""
        ctx = PolicyContext.for_testing(transaction_id="txn-456")

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=None)

        with patch("luthien_proxy.policy_core.policy_context._tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value = mock_span

            with ctx.span("my_operation"):
                pass

            mock_span.set_attribute.assert_any_call("luthien.transaction_id", "txn-456")

    def test_span_accepts_custom_attributes(self):
        """Custom attributes are set on the span."""
        ctx = PolicyContext.for_testing(transaction_id="test-123")

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=None)

        with patch("luthien_proxy.policy_core.policy_context._tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value = mock_span

            with ctx.span("check", attributes={"policy.check_type": "safety", "policy.score": 0.95}):
                pass

            # Check that custom attributes were set
            mock_span.set_attribute.assert_any_call("policy.check_type", "safety")
            mock_span.set_attribute.assert_any_call("policy.score", 0.95)

    def test_span_yields_span_for_further_customization(self):
        """The yielded span can be used to add events and attributes."""
        ctx = PolicyContext.for_testing(transaction_id="test-123")

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=None)

        with patch("luthien_proxy.policy_core.policy_context._tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value = mock_span

            with ctx.span("complex_check") as span:
                span.set_attribute("policy.result", "blocked")
                span.add_event("policy.decision_made", {"reason": "unsafe content"})

            mock_span.set_attribute.assert_any_call("policy.result", "blocked")
            mock_span.add_event.assert_called_once_with("policy.decision_made", {"reason": "unsafe content"})


class TestPolicyContextAddSpanEvent:
    """Tests for PolicyContext.add_span_event() method."""

    def test_add_span_event_adds_to_current_span(self):
        """Events are added to the current span."""
        ctx = PolicyContext.for_testing(transaction_id="test-123")

        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        with patch.object(trace, "get_current_span", return_value=mock_span):
            ctx.add_span_event("policy.content_checked")

        mock_span.add_event.assert_called_once_with("policy.content_checked", attributes={})

    def test_add_span_event_with_attributes(self):
        """Events can include attributes."""
        ctx = PolicyContext.for_testing(transaction_id="test-123")

        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        with patch.object(trace, "get_current_span", return_value=mock_span):
            ctx.add_span_event("policy.sql_detected", {"pattern": "DROP TABLE", "confidence": 0.99})

        mock_span.add_event.assert_called_once_with(
            "policy.sql_detected", attributes={"pattern": "DROP TABLE", "confidence": 0.99}
        )

    def test_add_span_event_no_op_when_not_recording(self):
        """Adding event when span is not recording is a no-op."""
        ctx = PolicyContext.for_testing(transaction_id="test-123")

        mock_span = MagicMock()
        mock_span.is_recording.return_value = False

        with patch.object(trace, "get_current_span", return_value=mock_span):
            ctx.add_span_event("policy.orphan_event")

        mock_span.add_event.assert_not_called()


class TestPolicyContextForTesting:
    """Tests for PolicyContext.for_testing() factory."""

    def test_for_testing_creates_valid_context(self):
        """for_testing() creates a usable PolicyContext."""
        ctx = PolicyContext.for_testing()
        assert ctx.transaction_id == "test-txn"
        assert ctx.request is None
        assert ctx.session_id is None

    def test_for_testing_accepts_custom_values(self):
        """for_testing() accepts custom transaction_id and session_id."""
        ctx = PolicyContext.for_testing(transaction_id="custom-txn", session_id="sess-123")
        assert ctx.transaction_id == "custom-txn"
        assert ctx.session_id == "sess-123"


class TestPolicyContextRecordEvent:
    """Tests for PolicyContext.record_event() method."""

    def test_record_event_calls_emitter(self):
        """record_event() calls emitter with transaction_id."""
        mock_emitter = MagicMock()
        ctx = PolicyContext(transaction_id="txn-789", emitter=mock_emitter)

        ctx.record_event("policy.action", {"key": "value"})

        mock_emitter.record.assert_called_once_with("txn-789", "policy.action", {"key": "value"})


class TestPolicyContextScratchpad:
    """Tests for PolicyContext.scratchpad property."""

    def test_scratchpad_is_mutable_dict(self):
        """scratchpad is a mutable dictionary."""
        ctx = PolicyContext.for_testing()

        ctx.scratchpad["key"] = "value"
        assert ctx.scratchpad["key"] == "value"

    def test_scratchpad_persists_across_accesses(self):
        """scratchpad retains values across multiple accesses."""
        ctx = PolicyContext.for_testing()

        ctx.scratchpad["counter"] = 0
        ctx.scratchpad["counter"] += 1
        ctx.scratchpad["counter"] += 1

        assert ctx.scratchpad["counter"] == 2

    def test_scratchpad_is_isolated_per_context(self):
        """Each context has its own scratchpad."""
        ctx1 = PolicyContext.for_testing(transaction_id="ctx1")
        ctx2 = PolicyContext.for_testing(transaction_id="ctx2")

        ctx1.scratchpad["value"] = "from ctx1"
        ctx2.scratchpad["value"] = "from ctx2"

        assert ctx1.scratchpad["value"] == "from ctx1"
        assert ctx2.scratchpad["value"] == "from ctx2"
