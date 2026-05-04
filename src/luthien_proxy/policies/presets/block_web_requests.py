"""Policy that blocks network/web request tool calls."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy


class BlockWebRequestsPolicy(SimpleLLMPolicy):
    """Blocks curl, wget, fetch, and similar network request tool calls.

    Prevents data exfiltration by blocking tool calls that make outbound
    network requests. Text content discussing these commands is allowed.
    """

    category = "active_monitoring"
    group = "blocks"
    display_name = "Block Web Requests"
    short_description = "Blocks curl, wget, and other outbound network requests."
    badges = ("Blocks",)

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=(
                    "Block tool calls that make outbound network requests. This includes "
                    "commands containing: curl, wget, fetch, httpie (http/https commands), "
                    "nc (netcat), ncat, socat, telnet, ssh, scp, rsync (to remote hosts), "
                    "python -c with urllib/requests/httpx, node -e with fetch/http, "
                    "or any command that sends data to an external URL. "
                    "When blocking, replace with a text block: "
                    "'[BLOCKED] Network request detected: <command>. Outbound network "
                    "requests are blocked by the safety policy.' "
                    "Text blocks that discuss or explain these commands should pass unchanged. "
                    "Only block actual tool_use calls that would execute them."
                ),
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=4096,
                on_error="block",
                auth_provider="user_credentials",
            )
        )
