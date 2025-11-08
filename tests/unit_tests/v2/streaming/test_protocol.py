# ABOUTME: Unit tests for PolicyContext
# ABOUTME: Tests scratchpad and transaction_id behavior

"""Tests for PolicyContext."""

from luthien_proxy.v2.policies import PolicyContext


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
