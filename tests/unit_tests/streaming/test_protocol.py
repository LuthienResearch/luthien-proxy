"""Unit tests for PolicyContext.

Tests scratchpad, transaction_id, session_id, and raw_http_request behavior.
"""

from luthien_proxy.policies import PolicyContext
from luthien_proxy.types import RawHttpRequest


class TestPolicyContext:
    """Tests for PolicyContext."""

    def test_initialization(self):
        """PolicyContext initializes with transaction_id and empty scratchpad."""
        ctx = PolicyContext(transaction_id="test-123")

        assert ctx.transaction_id == "test-123"
        assert ctx.scratchpad == {}
        assert isinstance(ctx.scratchpad, dict)

    def test_scratchpad_is_mutable(self):
        """Scratchpad can be modified to store arbitrary state."""
        ctx = PolicyContext(transaction_id="test-456")

        # Can add items
        ctx.scratchpad["key1"] = "value1"
        ctx.scratchpad["key2"] = 42
        ctx.scratchpad["nested"] = {"inner": "data"}

        assert ctx.scratchpad["key1"] == "value1"
        assert ctx.scratchpad["key2"] == 42
        assert ctx.scratchpad["nested"]["inner"] == "data"

        # Can modify items
        ctx.scratchpad["key1"] = "updated"
        assert ctx.scratchpad["key1"] == "updated"

        # Can delete items
        del ctx.scratchpad["key2"]
        assert "key2" not in ctx.scratchpad

    def test_scratchpad_persists(self):
        """Scratchpad is the same object across property accesses."""
        ctx = PolicyContext(transaction_id="test-789")

        ctx.scratchpad["data"] = "persistent"
        scratchpad_ref = ctx.scratchpad

        # Same object identity
        assert ctx.scratchpad is scratchpad_ref
        assert ctx.scratchpad["data"] == "persistent"

    def test_different_contexts_have_separate_scratchpads(self):
        """Each PolicyContext instance has its own scratchpad."""
        ctx1 = PolicyContext(transaction_id="ctx-1")
        ctx2 = PolicyContext(transaction_id="ctx-2")

        ctx1.scratchpad["data"] = "context 1"
        ctx2.scratchpad["data"] = "context 2"

        assert ctx1.scratchpad["data"] == "context 1"
        assert ctx2.scratchpad["data"] == "context 2"
        assert ctx1.scratchpad is not ctx2.scratchpad

    def test_session_id_initialization(self):
        """PolicyContext stores session_id when provided."""
        ctx = PolicyContext(transaction_id="test-123", session_id="session-abc-123")

        assert ctx.session_id == "session-abc-123"

    def test_session_id_defaults_to_none(self):
        """PolicyContext defaults session_id to None when not provided."""
        ctx = PolicyContext(transaction_id="test-123")

        assert ctx.session_id is None

    def test_raw_http_request_initialization(self):
        """PolicyContext stores raw_http_request when provided."""
        raw_request = RawHttpRequest(
            body={"model": "gpt-4", "messages": []},
            headers={"content-type": "application/json"},
            method="POST",
            path="/v1/chat/completions",
        )
        ctx = PolicyContext(transaction_id="test-123", raw_http_request=raw_request)

        assert ctx.raw_http_request is raw_request
        assert ctx.raw_http_request.method == "POST"
        assert ctx.raw_http_request.path == "/v1/chat/completions"

    def test_raw_http_request_defaults_to_none(self):
        """PolicyContext defaults raw_http_request to None when not provided."""
        ctx = PolicyContext(transaction_id="test-123")

        assert ctx.raw_http_request is None


class TestPolicyContextForTesting:
    """Tests for PolicyContext.for_testing() factory method."""

    def test_for_testing_defaults(self):
        """for_testing() creates context with sensible defaults."""
        ctx = PolicyContext.for_testing()

        assert ctx.transaction_id == "test-txn"
        assert ctx.session_id is None
        assert ctx.raw_http_request is None
        assert ctx.scratchpad == {}

    def test_for_testing_with_session_id(self):
        """for_testing() accepts session_id parameter."""
        ctx = PolicyContext.for_testing(session_id="test-session-456")

        assert ctx.session_id == "test-session-456"

    def test_for_testing_with_raw_http_request(self):
        """for_testing() accepts raw_http_request parameter."""
        raw_request = RawHttpRequest(
            body={"model": "claude-3-opus"},
            headers={"x-session-id": "test-session"},
            method="POST",
            path="/v1/messages",
        )
        ctx = PolicyContext.for_testing(raw_http_request=raw_request)

        assert ctx.raw_http_request is raw_request
        assert ctx.raw_http_request.body == {"model": "claude-3-opus"}

    def test_for_testing_with_all_parameters(self):
        """for_testing() accepts all parameters."""
        raw_request = RawHttpRequest(
            body={},
            headers={},
            method="POST",
            path="/v1/messages",
        )
        ctx = PolicyContext.for_testing(
            transaction_id="custom-txn",
            session_id="session-xyz",
            raw_http_request=raw_request,
        )

        assert ctx.transaction_id == "custom-txn"
        assert ctx.session_id == "session-xyz"
        assert ctx.raw_http_request is raw_request
