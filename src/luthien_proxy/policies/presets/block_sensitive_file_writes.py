"""Policy that blocks file writes to sensitive system paths."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class BlockSensitiveFileWritesPolicy(SimpleLLMPolicy):
    """Blocks file write operations targeting sensitive paths like /etc, ~/.ssh, ~/.gnupg.

    Prevents accidental or malicious modification of system configuration,
    SSH keys, GPG keys, and other security-sensitive files.
    """

    category = "active_monitoring"
    display_name = "Block File Writes"
    short_description = "Blocks file writes to sensitive paths like /etc, ~/.ssh, ~/.gnupg."
    badges = ("Blocks",)

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "Block tool calls that write to sensitive file paths. Block writes to: "
                    "/etc/, /usr/, /boot/, /sys/, /proc/, ~/.ssh/, ~/.gnupg/, ~/.aws/, "
                    "~/.config/gcloud/, ~/.kube/, ~/.docker/, /root/, "
                    "any path containing .pem, .key, .crt, id_rsa, id_ed25519, "
                    "authorized_keys, known_hosts, shadow, passwd, sudoers. "
                    "This applies to tool calls using write/edit/tee/redirect (>) to these paths. "
                    "Reading from these paths is allowed. "
                    "When blocking, replace with a text block: "
                    "'[BLOCKED] Write to sensitive path detected: <path>. Writes to "
                    "system and security files are blocked by the safety policy.' "
                    "Text blocks discussing these paths should pass unchanged."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="block",
                auth_provider="user_credentials",
            )
        )
