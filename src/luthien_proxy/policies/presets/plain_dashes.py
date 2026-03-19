"""Policy that replaces em-dashes with regular dashes."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class PlainDashesPolicy(SimpleLLMPolicy):
    """Replaces em-dashes and en-dashes with plain hyphens in LLM responses.

    Converts Unicode em-dashes and en-dashes to regular hyphens.
    Useful for terminal environments where Unicode dashes render poorly.
    """

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "Replace all em-dashes (\u2014) and en-dashes (\u2013) with regular "
                    "hyphens/dashes (-). Do not change any other content. If there are "
                    "no em-dashes or en-dashes, pass the block unchanged."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="pass",
            )
        )
