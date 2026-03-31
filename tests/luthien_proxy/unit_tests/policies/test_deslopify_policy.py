"""Unit tests for DeslopifyPolicy.

Tests the deslopify function comprehensively across opener, closer, and inline
pattern categories, plus edge cases. Protocol compliance and Anthropic response
integration are tested lightly since TextModifierPolicy handles the plumbing.
"""

from typing import cast

import pytest
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.llm.types.anthropic import (
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.deslopify_policy import DeslopifyPolicy, deslopify
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    TextModifierPolicy,
)
from luthien_proxy.policy_core.base_policy import BasePolicy


@pytest.fixture
def policy():
    return DeslopifyPolicy()


# =============================================================================
# Protocol Compliance
# =============================================================================


class TestProtocol:
    def test_inherits_text_modifier_policy(self, policy):
        assert isinstance(policy, TextModifierPolicy)

    def test_inherits_base_policy(self, policy):
        assert isinstance(policy, BasePolicy)

    def test_implements_anthropic_interface(self, policy):
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_modify_text_delegates_to_deslopify(self, policy):
        assert policy.modify_text("Great question! The answer is 42.") == "The answer is 42."


# =============================================================================
# Openers
# =============================================================================


class TestOpeners:
    """Test sycophantic opener removal."""

    @pytest.mark.parametrize(
        "inp,expected",
        [
            # Sycophantic affirmations
            ("Great question! The answer is X.", "The answer is X."),
            ("Excellent point! Here is the fix.", "Here is the fix."),
            ("That's a great question. The answer is X.", "The answer is X."),
            ("What a fantastic observation! The fix is Y.", "The fix is Y."),
            ("That's a really great observation. The fix is Y.", "The fix is Y."),
            # "Interesting thought!" is an opener, "Let me explain." is also an opener → both stripped
            ("Interesting thought! Let me explain.", ""),
            ("Interesting thought! The answer is X.", "The answer is X."),
            # One-word affirmations
            ("Absolutely! The answer is X.", "The answer is X."),
            ("Certainly! Here is the code.", "Here is the code."),
            ("Of course! The fix is simple.", "The fix is simple."),
            ("Sure! Here you go.", "Here you go."),
            ("Sure thing! The answer is X.", "The answer is X."),
            ("Definitely! That works.", "That works."),
            ("Indeed! The answer is X.", "The answer is X."),
            ("Exactly! That's correct.", "That's correct."),
            # Enthusiasm / willingness
            ("I'd be happy to help! The answer is X.", "The answer is X."),
            ("I'd love to help with that. Here is the code.", "Here is the code."),
            ("I'm happy to explain. The issue is X.", "The issue is X."),
            ("I'm glad to assist with that. Here is the fix.", "Here is the fix."),
            ("I'd be delighted to help! Let me show you.", "Let me show you."),
            # "help you understand" special case
            ("I'd be happy to help you understand how X works.", "How X works."),
            # Gratitude
            ("Thank you for asking! The answer is X.", "The answer is X."),
            ("Thanks for sharing! The issue is Y.", "The issue is Y."),
            ("Thank you for asking about this! The fix is Z.", "The fix is Z."),
            ("Thanks for the great question! Here is info.", "Here is info."),
            # Meta-commentary
            ("Let me explain. The issue is X.", "The issue is X."),
            ("Let me walk you through this. First, you need X.", "First, you need X."),
            ("Let me break this down. The answer is X.", "The answer is X."),
            ("Let me dive in. The answer is X.", "The answer is X."),
            # "Here's the thing" / "Here's what you need to know"
            ("Here's the thing: the answer is X.", "The answer is X."),
            ("Here's what you need to know: X is broken.", "X is broken."),
            # Filler starters
            ("So, the answer is X.", "The answer is X."),
            ("Well, the answer is X.", "The answer is X."),
            ("So basically, the issue is Y.", "The issue is Y."),
            # "To answer your question"
            ("To answer your question, the fix is X.", "The fix is X."),
            # Stacked openers
            (
                "Absolutely! Great question! I'd be happy to help! Here is the answer.",
                "Here is the answer.",
            ),
            (
                "Certainly! That's a great point. Let me explain. The issue is X.",
                "The issue is X.",
            ),
        ],
    )
    def test_opener_removal(self, inp, expected):
        assert deslopify(inp) == expected

    def test_opener_only_matches_at_start(self):
        text = "The answer is certainly correct."
        assert deslopify(text) == text

    def test_opener_does_not_match_mid_sentence(self):
        text = "I think that's a great question to explore in the next meeting."
        assert deslopify(text) == text


# =============================================================================
# Closers
# =============================================================================


class TestClosers:
    """Test hollow closer removal."""

    @pytest.mark.parametrize(
        "inp,expected",
        [
            # Hope/help closers
            ("The answer is X. I hope this helps!", "The answer is X."),
            ("The answer is X. Hope this helps!", "The answer is X."),
            ("The answer is X. I hope this was helpful!", "The answer is X."),
            ("The answer is X. I hope that makes sense!", "The answer is X."),
            # Feel free / don't hesitate
            ("Done. Feel free to ask if you have questions!", "Done."),
            ("Done. Please feel free to reach out!", "Done."),
            ("Done. Don't hesitate to let me know!", "Done."),
            # Let me know
            ("Result is X. Let me know if you need anything else!", "Result is X."),
            ("Result is X. Let me know if you have questions!", "Result is X."),
            # If you have questions
            ("Done. If you have any other questions, I'm here.", "Done."),
            ("Done. If you have further questions, just ask.", "Done."),
            # Happy X-ing
            ("Code works. Happy coding!", "Code works."),
            ("See you. Happy learning!", "See you."),
            # I'm here to help
            ("Done. I'm here if you need anything!", "Done."),
            ("Done. I'm always here to help!", "Done."),
            # Good luck
            ("Here's the fix. Good luck!", "Here's the fix."),
            ("Try that. Best of luck with your project!", "Try that."),
            # Is there anything else
            ("Done. Is there anything else I can help you with?", "Done."),
            # Stacked closers
            (
                "The answer is 42. I hope this helps! Feel free to ask! Happy coding!",
                "The answer is 42.",
            ),
        ],
    )
    def test_closer_removal(self, inp, expected):
        assert deslopify(inp) == expected

    def test_closer_does_not_match_mid_text(self):
        text = "Let me know when the server is ready."
        assert deslopify(text) == text


# =============================================================================
# Inline Filler
# =============================================================================


class TestInlineFiller:
    """Test inline filler phrase removal."""

    @pytest.mark.parametrize(
        "inp,expected",
        [
            # AI self-reference
            ("As an AI language model, I think X.", "I think X."),
            ("As a large language model, I can explain.", "I can explain."),
            ("As an AI, I don't have feelings.", "I don't have feelings."),
            ("As an AI assistant, I can help.", "I can help."),
            # Filler hedges
            ("It's worth noting that X is true.", "X is true."),
            ("It's important to note that X.", "X."),
            ("It is worth mentioning that the API changed.", "The API changed."),
            ("It should be noted that X is fragile.", "X is fragile."),
            ("I should mention that X matters.", "X matters."),
            ("I should point out that this is slow.", "This is slow."),
            ("I want to emphasize that X is critical.", "X is critical."),
            ("I'd like to point out that Y is wrong.", "Y is wrong."),
            # Transition filler
            ("The API is fast. Essentially, it caches everything.", "The API is fast. It caches everything."),
            ("Basically, the issue is X.", "The issue is X."),
            ("In essence, the fix is simple.", "The fix is simple."),
            ("In other words, X equals Y.", "X equals Y."),
            ("Put simply, the algorithm is O(n).", "The algorithm is O(n)."),
            ("At the end of the day, performance matters.", "Performance matters."),
            ("In summary, the fix works.", "The fix works."),
            ("To summarize, X is the answer.", "X is the answer."),
            ("In conclusion, use connection pooling.", "Use connection pooling."),
            ("All in all, the approach works.", "The approach works."),
            # "The key takeaway"
            (
                "The key takeaway is that caching helps.",
                "Caching helps.",
            ),
            ("The main thing to understand is that X matters.", "X matters."),
            ("The main point is that X matters.", "X matters."),
            # Redundant certainty
            (
                "It's important to understand that X is expensive.",
                "X is expensive.",
            ),
            ("Keep in mind that the pool has limits.", "The pool has limits."),
            ("Bear in mind that this is slow.", "This is slow."),
            ("It goes without saying that tests matter.", "Tests matter."),
            ("Needless to say, the fix worked.", "The fix worked."),
        ],
    )
    def test_inline_removal(self, inp, expected):
        assert deslopify(inp) == expected

    def test_curly_quote_support(self):
        assert deslopify("It\u2019s worth noting that X.") == "X."

    def test_capitalization_after_removal(self):
        result = deslopify("First sentence. It's worth noting that the cache is fast.")
        assert result == "First sentence. The cache is fast."

    def test_inline_mid_paragraph(self):
        text = "Setup is done.\n\nIt's worth noting that you need a key. The rest is easy."
        result = deslopify(text)
        assert result == "Setup is done.\n\nYou need a key. The rest is easy."


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    def test_empty_string(self):
        assert deslopify("") == ""

    def test_whitespace_only(self):
        assert deslopify("   ") == ""

    def test_all_slop_returns_empty(self):
        assert deslopify("Great question! I hope this helps!") == ""

    def test_clean_text_unchanged(self):
        text = "The function takes O(n log n) time because it sorts the input array first."
        assert deslopify(text) == text

    def test_code_block_content_preserved(self):
        text = '```python\nprint("Certainly! Great question!")\n```'
        assert deslopify(text) == text

    def test_preserves_periods_after_closer_removal(self):
        assert deslopify("Python uses reference counting. I hope this helps!") == "Python uses reference counting."

    def test_multiline_with_code_block(self):
        text = """As an AI language model, here is the code:

```python
def hello():
    print("hello")
```

The function prints hello."""
        result = deslopify(text)
        assert "```python" in result
        assert 'print("hello")' in result
        assert result.startswith("Here is the code:")

    def test_does_not_over_capitalize(self):
        text = "the variable x is lowercase on purpose."
        result = deslopify(text)
        # Should capitalize the start since it's the beginning of text
        assert result == "The variable x is lowercase on purpose."

    def test_multiple_blank_lines_collapsed(self):
        text = "First.\n\n\n\n\nSecond."
        assert deslopify(text) == "First.\n\nSecond."


# =============================================================================
# Realistic Full Responses
# =============================================================================


class TestRealisticResponses:
    def test_typical_sloppy_response(self):
        text = (
            "Certainly! That's a great question. "
            "Connection pooling is a technique where database connections are reused. "
            "It's important to understand that creating connections is expensive. "
            "In summary, always use a pool in production. "
            "I hope this helps! Happy coding!"
        )
        result = deslopify(text)
        assert "Certainly" not in result
        assert "great question" not in result
        assert "important to understand that" not in result
        assert "In summary, " not in result
        assert "hope this helps" not in result
        assert "Happy coding" not in result
        # Core content preserved
        assert "Connection pooling is a technique" in result
        assert "Creating connections is expensive." in result
        assert "Always use a pool in production." in result

    def test_clean_technical_response_untouched(self):
        text = (
            "The function takes O(n log n) time because it sorts the input array first, "
            "then performs a binary search for each element. The sorting dominates "
            "the overall complexity.\n\n"
            "```python\ndef search(arr, target):\n    arr.sort()\n    return binary_search(arr, target)\n```"
        )
        assert deslopify(text) == text


# =============================================================================
# Anthropic Response Integration
# =============================================================================


class TestAnthropicResponse:
    @pytest.mark.asyncio
    async def test_transforms_text_block(self):
        policy = DeslopifyPolicy()
        text_block: AnthropicTextBlock = {
            "type": "text",
            "text": "Great question! The answer is 42.",
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        policy._modify_anthropic_response(response)
        assert cast(AnthropicTextBlock, response["content"][0])["text"] == "The answer is 42."

    @pytest.mark.asyncio
    async def test_leaves_tool_use_unchanged(self):
        policy = DeslopifyPolicy()
        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "run_code",
            "input": {"code": "Great question! print('hello')"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        policy._modify_anthropic_response(response)
        result = cast(AnthropicToolUseBlock, response["content"][0])
        assert result["input"] == {"code": "Great question! print('hello')"}
