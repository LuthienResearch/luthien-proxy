"""Policy that enforces concise responses by cutting filler and hedging."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class NoYappingPolicy(SimpleLLMPolicy):
    """Enforces concise responses by removing filler, hedging, and unnecessary preamble.

    Cuts phrases like 'Certainly!', 'Great question!', 'Let me explain...',
    and excessive qualifiers, leaving only substantive content.
    """

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "Make the text more concise by removing filler and hedging. Remove: "
                    "- Opening pleasantries: 'Certainly!', 'Of course!', 'Great question!', "
                    "  'Sure thing!', 'Absolutely!', 'Happy to help!' "
                    "- Unnecessary preamble: 'Let me explain...', 'I'll walk you through...', "
                    "  'Here's what I think...', 'To answer your question...' "
                    "- Excessive hedging: 'I think maybe', 'It might be possible that', "
                    "  'Perhaps you could consider' (keep one level of hedging if genuinely uncertain) "
                    "- Trailing summaries: 'Let me know if you have any questions', "
                    "  'I hope this helps!', 'Feel free to ask if...' "
                    "Preserve all substantive content. Do not change tool calls. "
                    "If the text is already concise, pass unchanged."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="pass",
            )
        )
