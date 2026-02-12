"""MultiSerialPolicy - Run multiple policies sequentially.

Each policy's output becomes the next policy's input, forming a pipeline.
Supports both OpenAI and Anthropic interfaces by checking which interfaces
each sub-policy implements.

Example config:
    policy:
      class: "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy"
      config:
        policies:
          - class: "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy"
            config: {}
          - class: "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
            config: {}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
    BasePolicy,
    OpenAIPolicyInterface,
    PolicyProtocol,
)

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


def _load_sub_policy(policy_config: dict[str, Any]) -> PolicyProtocol:
    """Load a single sub-policy from its config dict.

    Reuses the existing config loading machinery so nested policies
    (including other Multi* policies) work recursively.
    """
    from luthien_proxy.config import _import_policy_class, _instantiate_policy  # noqa: PLC0415

    class_ref = policy_config["class"]
    config = policy_config.get("config", {})
    policy_class = _import_policy_class(class_ref)
    return _instantiate_policy(policy_class, config)


class MultiSerialPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """Run multiple policies sequentially, piping each output to the next.

    For requests: request flows through policy1 -> policy2 -> ... -> policyN
    For responses: response flows through policy1 -> policy2 -> ... -> policyN

    Only delegates to sub-policies that implement the relevant interface.
    For example, on_openai_request only calls sub-policies that implement
    OpenAIPolicyInterface.
    """

    def __init__(self, policies: list[dict[str, Any]]) -> None:
        """Initialize with a list of policy config dicts to run in sequence."""
        self._sub_policies: list[PolicyProtocol] = [_load_sub_policy(cfg) for cfg in policies]
        names = [p.short_policy_name for p in self._sub_policies]
        logger.info(f"MultiSerialPolicy initialized with {len(self._sub_policies)} policies: {names}")

    @property
    def short_policy_name(self) -> str:
        """Human-readable name showing the pipeline composition."""
        names = [p.short_policy_name for p in self._sub_policies]
        return f"MultiSerial({', '.join(names)})"

    # =========================================================================
    # OpenAI Interface
    # =========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Chain request through each OpenAI-compatible sub-policy."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                request = await policy.on_openai_request(request, context)
        return request

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Chain response through each OpenAI-compatible sub-policy."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                response = await policy.on_openai_response(response, context)
        return response

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_chunk_received(ctx)

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_content_delta(ctx)

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_content_complete(ctx)

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_tool_call_delta(ctx)

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_tool_call_complete(ctx)

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_finish_reason(ctx)

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_stream_complete(ctx)

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each OpenAI-compatible sub-policy in sequence."""
        for policy in self._sub_policies:
            if isinstance(policy, OpenAIPolicyInterface):
                await policy.on_streaming_policy_complete(ctx)

    # =========================================================================
    # Anthropic Interface
    # =========================================================================

    async def on_anthropic_request(
        self, request: "AnthropicRequest", context: "PolicyContext"
    ) -> "AnthropicRequest":
        """Chain request through each Anthropic-compatible sub-policy."""
        for policy in self._sub_policies:
            if isinstance(policy, AnthropicPolicyInterface):
                request = await policy.on_anthropic_request(request, context)
        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Chain response through each Anthropic-compatible sub-policy."""
        for policy in self._sub_policies:
            if isinstance(policy, AnthropicPolicyInterface):
                response = await policy.on_anthropic_response(response, context)
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> list[AnthropicStreamEvent]:
        """Chain Anthropic stream events through sub-policies sequentially.

        Each sub-policy can transform or filter events. The output events from
        one policy become the input events for the next. If any policy filters
        out all events (returns []), the chain short-circuits.
        """
        events = [event]
        for policy in self._sub_policies:
            if not isinstance(policy, AnthropicPolicyInterface):
                continue
            next_events: list[AnthropicStreamEvent] = []
            for evt in events:
                next_events.extend(await policy.on_anthropic_stream_event(evt, context))
            events = next_events
            if not events:
                break
        return events


__all__ = ["MultiSerialPolicy"]
