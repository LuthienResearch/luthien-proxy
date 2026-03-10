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
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent

from luthien_proxy.policies.multi_policy_utils import load_sub_policy, validate_sub_policies_interface
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
    BasePolicy,
    OpenAIPolicyInterface,
    PolicyProtocol,
)

if TYPE_CHECKING:
    from typing import Any

    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


class MultiSerialPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Run multiple policies sequentially, piping each output to the next.

    Both requests and responses flow through policies in list order:
        policy1 -> policy2 -> ... -> policyN

    For Anthropic execution this is a two-phase model:
      1. Request phase: on_anthropic_request hooks run in list order before the LLM call.
      2. Response phase: on_anthropic_response / on_anthropic_stream_event hooks run
         in list order after the LLM call.

    Example: [StringReplacement, AllCaps] on a response applies StringReplacement first,
    then AllCaps — both in list order.

    All sub-policies must implement the interface being called.
    """

    def __init__(self, policies: list[dict[str, Any]]) -> None:
        """Initialize with a list of policy config dicts to run in sequence."""
        self._sub_policies: tuple[PolicyProtocol, ...] = tuple(load_sub_policy(cfg) for cfg in policies)
        if not self._sub_policies:
            logger.warning(
                "MultiSerialPolicy initialized with empty policy list — requests will pass through unchanged"
            )
        names = [p.short_policy_name for p in self._sub_policies]
        logger.info(f"MultiSerialPolicy initialized with {len(self._sub_policies)} policies: {names}")

    @classmethod
    def from_instances(cls, policies: list["PolicyProtocol"]) -> "MultiSerialPolicy":
        """Create from pre-instantiated policy objects.

        Use this when you already have policy instances (e.g. from runtime
        composition) and don't need config-based loading.
        """
        instance = object.__new__(cls)
        instance._sub_policies = tuple(policies)
        if not instance._sub_policies:
            logger.warning(
                "MultiSerialPolicy initialized with empty policy list — requests will pass through unchanged"
            )
        names = [p.short_policy_name for p in instance._sub_policies]
        logger.info(f"MultiSerialPolicy composed from {len(instance._sub_policies)} policies: {names}")
        return instance

    @property
    def short_policy_name(self) -> str:
        """Human-readable name showing the pipeline composition."""
        names = [p.short_policy_name for p in self._sub_policies]
        return f"MultiSerial({', '.join(names)})"

    def _validate_interface(self, interface: type, interface_name: str) -> None:
        """Raise TypeError if any sub-policy doesn't implement the required interface."""
        validate_sub_policies_interface(self._sub_policies, interface, interface_name, "MultiSerialPolicy")

    def _iter_execution_policies(self) -> list[AnthropicExecutionInterface]:
        """Return sub-policies as AnthropicExecutionInterface after runtime validation."""
        self._validate_interface(AnthropicExecutionInterface, "AnthropicExecutionInterface")
        return [policy for policy in self._sub_policies if isinstance(policy, AnthropicExecutionInterface)]

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
    # Anthropic execution interface
    # =========================================================================

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Execute sub-policies serially in list order for both request and response phases.

        Uses a two-phase model: all request transforms run in list order, then the backend
        is called once, then all response transforms run in list order. This gives consistent
        ordering for both request and response (unlike the onion/wrapping model which reverses
        response order).
        """
        if not self._iter_execution_policies():
            return self._passthrough_anthropic(io)

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            # Phase 1: chain request transforms in list order
            request = await self.on_anthropic_request(io.request, context)
            io.set_request(request)

            if request.get("stream", False):
                # Phase 2+3a: stream backend and apply per-event transforms in list order
                async for event in io.stream(request):
                    for e in await self.on_anthropic_stream_event(event, context):
                        yield e
                # Phase 3b: post-stream additions from each sub-policy in list order
                for e in await self.on_anthropic_stream_complete(context):
                    yield e
                return

            # Non-streaming: call backend then chain response transforms in list order
            response = await io.complete(request)
            yield await self.on_anthropic_response(response, context)

        return _run()

    def _passthrough_anthropic(self, io: AnthropicPolicyIOProtocol) -> AsyncIterator[AnthropicPolicyEmission]:
        """Run without sub-policies as a direct backend passthrough."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            request = io.request
            if request.get("stream", False):
                async for event in io.stream(request):
                    yield event
                return
            yield await io.complete(request)

        return _run()

    # =========================================================================
    # Anthropic helper hooks (for policy-level unit tests)
    # =========================================================================

    async def on_anthropic_request(self, request: AnthropicRequest, context: "PolicyContext") -> AnthropicRequest:
        """Chain request helper hooks through each sub-policy."""
        self._validate_interface(AnthropicExecutionInterface, "AnthropicExecutionInterface")
        for policy in self._sub_policies:
            request = await policy.on_anthropic_request(request, context)  # type: ignore[attr-defined]
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: "PolicyContext") -> AnthropicResponse:
        """Chain response helper hooks through each sub-policy."""
        self._validate_interface(AnthropicExecutionInterface, "AnthropicExecutionInterface")
        for policy in self._sub_policies:
            response = await policy.on_anthropic_response(response, context)  # type: ignore[attr-defined]
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Chain streaming helper hooks through each sub-policy."""
        self._validate_interface(AnthropicExecutionInterface, "AnthropicExecutionInterface")
        events = [event]
        for policy in self._sub_policies:
            next_events: list[MessageStreamEvent] = []
            for evt in events:
                next_events.extend(await policy.on_anthropic_stream_event(evt, context))  # type: ignore[attr-defined]
            events = next_events
            if not events:
                break
        return events

    async def on_anthropic_stream_complete(self, context: "PolicyContext") -> list[AnthropicPolicyEmission]:
        """Collect post-stream events from each sub-policy in list order."""
        all_events: list[AnthropicPolicyEmission] = []
        for policy in self._sub_policies:
            hook = getattr(policy, "on_anthropic_stream_complete", None)
            if hook is not None:
                events = await hook(context)
                if events:
                    all_events.extend(events)
        return all_events

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Delegate Anthropic cleanup helper hook to sub-policies when present."""
        for policy in self._sub_policies:
            maybe_hook = getattr(policy, "on_anthropic_streaming_policy_complete", None)
            if maybe_hook is not None:
                await maybe_hook(context)


__all__ = ["MultiSerialPolicy"]
