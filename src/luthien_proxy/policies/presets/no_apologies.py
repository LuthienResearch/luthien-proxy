"""Policy that removes excessive apologetic language from responses."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class NoApologiesPolicy(SimpleLLMPolicy):
    """Removes apologetic filler like 'I apologize' and 'I'm sorry' from responses.

    Strips common apologetic phrases that add no value, keeping the response
    direct and focused on the actual content.
    """

    category = "fun_and_goofy"
    display_name = "No Apologies"
    short_description = "Removes 'I apologize', 'I'm sorry', and other apologetic filler."
    badges = ()

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "Remove apologetic filler phrases from text content. This includes: "
                    "'I apologize', 'I'm sorry', 'Sorry about that', 'My apologies', "
                    "'I apologize for the confusion', 'I'm sorry for the error', "
                    "'Sorry for the inconvenience', and similar phrases. "
                    "Remove the entire sentence containing the apology if the sentence "
                    "is purely apologetic. If the apology is embedded in a useful sentence, "
                    "rewrite the sentence without the apology while preserving the useful content. "
                    "Do not change tool calls. If there are no apologies, pass unchanged."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="pass",
                auth_provider="user_credentials",
            )
        )
