"""My Hackathon Policy — [describe what it does here].

To activate via admin API (no restart needed):
    curl -X POST http://localhost:8000/api/admin/policy/set \
      -H "Authorization: Bearer $ADMIN_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"policy_class_ref": "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"}'

Or update config/policy_config.yaml and restart the gateway:
    policy:
      class: "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"
      config: {}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from luthien_proxy.policies.simple_policy import SimplePolicy

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicToolUseBlock
    from luthien_proxy.policy_core.policy_context import PolicyContext


class HackathonPolicy(SimplePolicy):
    """My hackathon policy.

    SimplePolicy buffers streaming content so you work with complete strings.
    Override any combination of these three methods:
    - simple_on_request: modify what the user sends to the LLM
    - simple_on_response_content: modify what the LLM sends back
    - simple_on_anthropic_tool_call: inspect/modify tool calls (file writes, shell commands, etc)

    For simpler text-only transforms, consider TextModifierPolicy instead
    (see all_caps_policy.py for a 27-line example).
    """

    async def simple_on_request(self, request_str: str, context: PolicyContext) -> str:
        """Transform the user's message before it reaches the LLM.

        Examples:
            return request_str + "\\n\\nAlways respond in haiku form."
            return request_str.replace("pip install", "uv pip install")
        """
        return request_str

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform the LLM's text response before the user sees it.

        Examples:
            return content.upper()
            return content + "\\n\\n[Processed by HackathonPolicy]"
        """
        return content

    async def simple_on_anthropic_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        """Inspect or modify tool calls (file writes, shell commands, etc).

        tool_call is a dict with keys: type, id, name, input
        Common tool names: "bash", "write", "edit", "read"

        Examples:
            if tool_call["name"] == "bash":
                cmd = tool_call["input"].get("command", "")
                if "rm -rf" in cmd:
                    tool_call["input"]["command"] = "echo 'Nice try!'"
        """
        return tool_call


__all__ = ["HackathonPolicy"]
