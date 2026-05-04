"""Policy that blocks dangerous shell commands in tool calls."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class BlockDangerousCommandsPolicy(SimpleLLMPolicy):
    """Blocks dangerous shell commands like rm -rf, chmod 777, mkfs, and dd.

    When the LLM attempts to execute potentially destructive commands via tool
    calls, the judge replaces them with an explanatory text block describing
    why the command was blocked.
    """

    category = "active_monitoring"
    group = "blocks"
    display_name = "Block Commands"
    short_description = "Blocks dangerous shell commands like rm -rf, chmod 777, and mkfs."
    badges = ("Blocks",)

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "Examine tool calls for dangerous shell commands. Block any tool call "
                    "that contains: rm -rf, rm -r (on broad paths like / or ~), chmod 777, "
                    "chmod -R 777, mkfs, dd if=, fdisk, parted, shred, wipefs, "
                    ":(){ :|:& };: (fork bomb), > /dev/sda, or similar destructive operations. "
                    "For text blocks mentioning these commands in explanations, pass them "
                    "unchanged. Only block actual tool_use calls that would execute them. "
                    "When blocking, replace with a text block explaining: "
                    "'[BLOCKED] Dangerous command detected: <command>. This command was "
                    "blocked by the safety policy.' "
                    "If the tool call is safe, pass it unchanged."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="block",
                auth_provider="user_credentials",
            )
        )
