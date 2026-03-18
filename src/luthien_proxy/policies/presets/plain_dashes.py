"""Policy that replaces em-dashes with regular dashes."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy


class PlainDashesPolicy(SimpleLLMPolicy):
    """Replaces em-dashes and en-dashes with plain hyphens in LLM responses.

    Converts Unicode em-dashes and en-dashes to regular hyphens.
    Useful for terminal environments where Unicode dashes render poorly.
    """

    def __init__(self) -> None:
        super().__init__(config={
            "instructions": (
                "Replace all em-dashes (\u2014) and en-dashes (\u2013) with regular "
                "hyphens/dashes (-). Do not change any other content. If there are "
                "no em-dashes or en-dashes, pass the block unchanged."
            ),
            "model": "claude-haiku-4-5",
            "temperature": 0.0,
            "max_tokens": 4096,
            "on_error": "pass",
        })
