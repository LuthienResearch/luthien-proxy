"""Unit tests for DeSlop policy."""

import pytest

from luthien_proxy.llm.types import Request
from luthien_proxy.policies.deslop_policy import DeSlop, DeSlopPolicy
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture
def policy():
    """Create a DeSlop policy instance with default config."""
    return DeSlop()


@pytest.fixture
def policy_context():
    """Create a basic policy context."""
    return PolicyContext(
        transaction_id="test-txn-123",
        request=Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        ),
    )


class TestDeSlop:
    """Test DeSlop policy text replacements."""

    async def test_em_dash_replacement(self, policy, policy_context):
        """Test that em-dashes are replaced with regular dashes."""
        content = "This is a test — with em-dashes — in the text."
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "This is a test - with em-dashes - in the text."

    async def test_en_dash_replacement(self, policy, policy_context):
        """Test that en-dashes are replaced with regular dashes."""
        content = "Pages 1–10 and 20–30"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "Pages 1-10 and 20-30"

    async def test_mixed_dashes(self, policy, policy_context):
        """Test that both em and en dashes are replaced."""
        content = "Em-dash — and en-dash – both replaced"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "Em-dash - and en-dash - both replaced"

    async def test_no_dashes_unchanged(self, policy, policy_context):
        """Test that content without special dashes is unchanged."""
        content = "Regular text with regular-dashes and no fancy stuff."
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == content

    async def test_empty_content(self, policy, policy_context):
        """Test that empty content is handled."""
        result = await policy.simple_on_response_content("", policy_context)
        assert result == ""

    async def test_multiple_consecutive_dashes(self, policy, policy_context):
        """Test handling of multiple consecutive em-dashes."""
        content = "This — is — a — heavily — dashed — sentence."
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "This - is - a - heavily - dashed - sentence."


class TestDeSlopCustomConfig:
    """Test custom configuration."""

    async def test_custom_replacements(self, policy_context):
        """Test that custom replacements are applied."""
        custom_policy = DeSlop(config={"replacements": {"utilize": "use", "leverage": "use"}})
        content = "We utilize this tool to leverage our capabilities."
        result = await custom_policy.simple_on_response_content(content, policy_context)
        assert result == "We use this tool to use our capabilities."

    async def test_custom_replaces_override_defaults(self, policy_context):
        """Test that custom replacements merge with defaults."""
        # Custom config adds new replacement, defaults still work
        custom_policy = DeSlop(config={"replacements": {"foo": "bar"}})
        content = "Test — em-dash and foo value"
        result = await custom_policy.simple_on_response_content(content, policy_context)
        # Both default (em-dash) and custom (foo) should be replaced
        assert result == "Test - em-dash and bar value"


class TestDeSlopPolicyAlias:
    """Test that DeSlopPolicy alias works."""

    def test_alias_exists(self):
        """Test that DeSlopPolicy is an alias for DeSlop."""
        assert DeSlopPolicy is DeSlop


class TestDeSlopInvariants:
    """Test policy invariants."""

    def test_policy_name(self, policy):
        """Test that policy has a readable name."""
        assert policy.short_policy_name == "DeSlop"

    async def test_preserves_newlines_and_formatting(self, policy, policy_context):
        """Test that newlines and other formatting are preserved."""
        content = "Line 1 — with dash\nLine 2 — with dash\n\tTabbed — line"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "Line 1 - with dash\nLine 2 - with dash\n\tTabbed - line"

    async def test_preserves_unicode(self, policy, policy_context):
        """Test that other unicode characters are preserved."""
        content = "Hello — world! 你好 — мир! 🎉 — emoji"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "Hello - world! 你好 - мир! 🎉 - emoji"

    async def test_code_blocks_processed(self, policy, policy_context):
        """Test that code blocks are also processed (no special handling)."""
        content = "```python\nresult = a — b  # em-dash in code\n```"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "```python\nresult = a - b  # em-dash in code\n```"


class TestDeSlopCurlyQuotes:
    """Test curly quote replacement (TDD: write tests first)."""

    async def test_curly_single_quotes_replaced(self, policy, policy_context):
        """Test that curly single quotes are replaced with straight quotes."""
        # \u2018 = ' (left single) \u2019 = ' (right single/apostrophe)
        content = "It\u2019s a \u2018test\u2019 with curly quotes"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "It's a 'test' with curly quotes"

    async def test_curly_double_quotes_replaced(self, policy, policy_context):
        """Test that curly double quotes are replaced with straight quotes."""
        # \u201c = " (left double) \u201d = " (right double)
        content = "He said \u201chello\u201d to her"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == 'He said "hello" to her'

    async def test_mixed_curly_quotes(self, policy, policy_context):
        """Test mixed curly quotes in same content."""
        content = "She said \u201cit\u2019s \u2018fine\u2019\u201d \u2014 really"
        result = await policy.simple_on_response_content(content, policy_context)
        assert result == "She said \"it's 'fine'\" - really"
