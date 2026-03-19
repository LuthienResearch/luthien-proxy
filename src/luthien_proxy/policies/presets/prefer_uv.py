"""Policy that replaces pip commands with uv equivalents."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class PreferUvPolicy(SimpleLLMPolicy):
    """Replaces pip commands with uv equivalents in LLM responses.

    When the LLM suggests pip-based commands (pip install, pip freeze, etc.),
    the judge replaces them with the uv equivalent (uv pip install, uv pip freeze, etc.).
    """

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "If the text or tool call contains pip commands (pip install, pip freeze, "
                    "pip uninstall, pip list, pip show, python -m pip, etc.), replace them with "
                    "their uv equivalents (uv pip install, uv pip freeze, uv pip uninstall, "
                    "uv pip list, uv pip show, etc.). Also replace 'pip' references in "
                    "explanatory text. If there are no pip references, pass the block unchanged."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="pass",
            )
        )
