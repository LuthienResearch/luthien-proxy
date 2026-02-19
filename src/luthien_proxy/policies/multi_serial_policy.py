"""MultiSerialPolicy - Run multiple policies sequentially.

Each policy's output becomes the next policy's input, forming a pipeline.
All sub-policies must implement the interface being called; a TypeError is
raised if any sub-policy is incompatible.

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
from typing import TYPE_CHECKING

from luthien_proxy.policies.multi_policy_utils import load_sub_policy, validate_sub_policies_interface
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
    BasePolicy,
    OpenAIPolicyInterface,
    PolicyProtocol,
)

if TYPE_CHECKING:
    from typing import Any

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


class MultiSerialPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """Run multiple policies sequentially, piping each output to the next.

    For requests: request flows through policy1 -> policy2 -> ... -> policyN
    For responses: response flows through policy1 -> policy2 -> ... -> policyN

    All sub-policies must implement the interface being called. If any sub-policy
    doesn't implement the required interface, a TypeError is raised at call time.
    """

    def __init__(self, policies: list[dict[str, Any]]) -> None:
        """Initialize with a list of policy config dicts to run in sequence."""
        self._sub_policies: list[PolicyProtocol] = [load_sub_policy(cfg) for cfg in policies]
        self._validated_interfaces: set[type] = set()
        if not self._sub_policies:
            # Warning (not error) because an empty list is a valid degenerate case:
            # the multi-policy becomes a no-op passthrough, which is safe and predictable.
            logger.warning(
                "MultiSerialPolicy initialized with empty policy list — requests will pass through unchanged"
            )
        names = [p.short_policy_name for p in self._sub_policies]
        logger.info(f"MultiSerialPolicy initialized with {len(self._sub_policies)} policies: {names}")

    @property
    def short_policy_name(self) -> str:
        """Human-readable name showing the pipeline composition."""
        names = [p.short_policy_name for p in self._sub_policies]
        return f"MultiSerial({', '.join(names)})"

    def _validate_interface(self, interface: type, interface_name: str) -> None:
        """Raise TypeError if any sub-policy doesn't implement the required interface."""
        validate_sub_policies_interface(
            self._sub_policies, self._validated_interfaces, interface, interface_name, "MultiSerialPolicy"
        )

    # =========================================================================
    # OpenAI Interface
    # =========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Chain request through each sub-policy."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            request = await policy.on_openai_request(request, context)  # type: ignore[union-attr]
        return request

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Chain response through each sub-policy."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            response = await policy.on_openai_response(response, context)  # type: ignore[union-attr]
        return response

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_chunk_received(ctx)  # type: ignore[union-attr]

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_content_delta(ctx)  # type: ignore[union-attr]

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_content_complete(ctx)  # type: ignore[union-attr]

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_tool_call_delta(ctx)  # type: ignore[union-attr]

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_tool_call_complete(ctx)  # type: ignore[union-attr]

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_finish_reason(ctx)  # type: ignore[union-attr]

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_stream_complete(ctx)  # type: ignore[union-attr]

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Delegate to each sub-policy in sequence."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        for policy in self._sub_policies:
            await policy.on_streaming_policy_complete(ctx)  # type: ignore[union-attr]

    # =========================================================================
    # Anthropic Interface
    # =========================================================================

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Chain request through each sub-policy."""
        self._validate_interface(AnthropicPolicyInterface, "AnthropicPolicyInterface")
        for policy in self._sub_policies:
            request = await policy.on_anthropic_request(request, context)  # type: ignore[union-attr]
        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Chain response through each sub-policy."""
        self._validate_interface(AnthropicPolicyInterface, "AnthropicPolicyInterface")
        for policy in self._sub_policies:
            response = await policy.on_anthropic_response(response, context)  # type: ignore[union-attr]
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> list[AnthropicStreamEvent]:
        """Chain Anthropic stream events through sub-policies sequentially.

        Each sub-policy can transform or filter events. The output events from
        one policy become the input events for the next. If any policy filters
        out all events (returns []), the chain short-circuits.
        """
        self._validate_interface(AnthropicPolicyInterface, "AnthropicPolicyInterface")
        events = [event]
        for policy in self._sub_policies:
            next_events: list[AnthropicStreamEvent] = []
            for evt in events:
                next_events.extend(await policy.on_anthropic_stream_event(evt, context))  # type: ignore[union-attr]
            events = next_events
            if not events:
                break
        return events


__all__ = ["MultiSerialPolicy"]
