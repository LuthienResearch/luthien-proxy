"""Policy that injects a conversation viewer link into the first response of each session.

On the first text response for a given session_id, prepends a line containing
the URL to the live conversation viewer. Subsequent responses in the same
session pass through unchanged.

Configuration:
    base_url: The proxy's base URL (e.g., "http://localhost:8000").

Example YAML:
    policy:
      class: "luthien_proxy.policies.conversation_link_policy:ConversationLinkPolicy"
      config:
        base_url: "http://localhost:8000"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from luthien_proxy.policies.simple_policy import SimplePolicy

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

# Module-level set tracking sessions that have already received a link.
# This avoids mutable instance state (which freeze_configured_state rejects)
# while surviving across requests within the same process.
_injected_sessions: set[str] = set()


class ConversationLinkPolicyConfig(BaseModel):
    base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL of the Luthien proxy for building viewer links",
    )


class ConversationLinkPolicy(SimplePolicy):
    """Injects a conversation viewer link into the first response of each session."""

    def __init__(self, base_url: str = "http://localhost:8000", **kwargs: object) -> None:
        """Initialize with base URL for building viewer links."""
        self._config = ConversationLinkPolicyConfig(base_url=base_url)

    @property
    def short_policy_name(self) -> str:
        """Return short name for logging and UI display."""
        return "ConversationLink"

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Prepend conversation viewer link to first response of each session."""
        session_id = context.session_id
        if not session_id:
            return content

        if session_id in _injected_sessions:
            return content

        _injected_sessions.add(session_id)
        base = self._config.base_url.rstrip("/")
        link = f"{base}/conversation/live/{session_id}"

        context.record_event(
            "policy.conversation_link.injected",
            {"link": link},
        )

        return f"[Conversation viewer: {link}]\n\n{content}"


__all__ = ["ConversationLinkPolicy"]
