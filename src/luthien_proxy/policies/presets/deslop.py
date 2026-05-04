"""Policy that removes AI writing tells from LLM responses."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class DeSlopPolicy(SimpleLLMPolicy):
    """Removes AI writing tells — em dashes, filler phrases, corporate tone, and clichés.

    Rewrites LLM responses to sound more natural and human. Catches common AI
    patterns like overuse of em dashes, "delve", "leverage", "I'd be happy to",
    and the relentless positivity that makes AI text instantly recognizable.
    """

    category = "simple_utilities"
    display_name = "De-Slop"
    short_description = "Removes AI writing tells — em dashes, filler phrases, corporate tone."
    badges = ()
    instructions_summary = (
        "Avoid AI writing tells such as em dashes. "
        "Replace with commas, semicolons, or periods. "
        "Avoid filler words like 'delve', 'leverage', 'utilize', 'comprehensive'. "
        "Remove opening pleasantries and trailing filler."
    )

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "Rewrite the text to remove AI writing tells while preserving the meaning. "
                    "Fix these specific patterns:\n"
                    "- Replace em dashes (\u2014) with commas, periods, or parentheses as appropriate\n"
                    "- Remove filler openings: 'Certainly!', 'Great question!', 'Absolutely!', "
                    "'I'd be happy to help!'\n"
                    "- Replace overused AI words: 'delve' \u2192 'explore/look at', "
                    "'leverage' \u2192 'use', 'utilize' \u2192 'use', "
                    "'facilitate' \u2192 'help/enable', 'comprehensive' \u2192 'complete/full', "
                    "'robust' \u2192 'strong/solid', 'streamline' \u2192 'simplify', "
                    "'pivotal' \u2192 'important/key', 'paradigm' \u2192 'model/approach'\n"
                    "- Remove unnecessary hedging: 'It's worth noting that', "
                    "'It's important to remember', 'Interestingly enough'\n"
                    "- Remove trailing filler: 'Let me know if you have any questions!', "
                    "'Hope this helps!', 'Feel free to reach out!'\n"
                    "- Reduce exclamation marks to at most one per paragraph\n"
                    "- If a list has more than 5 items, keep only the most important ones\n"
                    "Do NOT change code blocks, tool calls, or technical content. "
                    "If the text already sounds natural, pass it unchanged."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="pass",
                auth_provider="user_credentials",
            )
        )
