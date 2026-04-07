"""MultiSerialPolicy - Run multiple policies sequentially.

Each policy's output becomes the next policy's input, forming a pipeline.
All sub-policies must implement AnthropicExecutionInterface; a TypeError is
raised if any sub-policy is incompatible.

The executor calls MultiSerialPolicy's hooks, which chain through sub-policy
hooks in list order.

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
from typing import TYPE_CHECKING, cast

from anthropic.lib.streaming import MessageStreamEvent

from luthien_proxy.policies.multi_policy_utils import load_sub_policy, validate_sub_policies_interface
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    BasePolicy,
)

if TYPE_CHECKING:
    from typing import Any

    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


class MultiSerialPolicy(BasePolicy, AnthropicExecutionInterface):
    """Run multiple policies sequentially, piping each output to the next.

    Both requests and responses flow through policies in list order:
        policy1 -> policy2 -> ... -> policyN

    For Anthropic execution this is a two-phase model:
      1. Request phase: on_anthropic_request hooks run in list order before the LLM call.
      2. Response phase: on_anthropic_response / on_anthropic_stream_event hooks run
         in list order after the LLM call.

    Example: [StringReplacement, AllCaps] on a response applies StringReplacement first,
    then AllCaps — both in list order.

    All sub-policies must implement AnthropicExecutionInterface.
    """

    category = "internal"
    display_name = "Policy Chain"
    short_description = "Runs multiple policies sequentially as a pipeline."

    def __init__(self, policies: list[dict[str, Any]]) -> None:
        """Initialize with a list of policy config dicts to run in sequence."""
        self._sub_policies: tuple[BasePolicy, ...] = tuple(load_sub_policy(cfg) for cfg in policies)
        if not self._sub_policies:
            logger.warning(
                "MultiSerialPolicy initialized with empty policy list — requests will pass through unchanged"
            )
        names = [p.short_policy_name for p in self._sub_policies]
        logger.info(f"MultiSerialPolicy initialized with {len(self._sub_policies)} policies: {names}")

    @classmethod
    def from_instances(cls, policies: list[BasePolicy]) -> "MultiSerialPolicy":
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

    def active_policy_names(self) -> list[str]:
        """Recurse into sub-policies for leaf names."""
        names: list[str] = []
        for p in self._sub_policies:
            names.extend(p.active_policy_names())
        return names

    def _validate_interface(self, interface: type, interface_name: str) -> None:
        """Raise TypeError if any sub-policy doesn't implement the required interface."""
        validate_sub_policies_interface(self._sub_policies, interface, interface_name, "MultiSerialPolicy")

    # =========================================================================
    # Anthropic lifecycle hooks
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
        """Collect post-stream events from each sub-policy, chaining through the rest.

        When policy A emits stream_complete events, those events pass through
        policies B, C, ... via on_anthropic_stream_event so the full chain applies.
        """
        all_events: list[AnthropicPolicyEmission] = []
        for i, policy in enumerate(self._sub_policies):
            hook = getattr(policy, "on_anthropic_stream_complete", None)
            if hook is None:
                continue
            events = await hook(context)
            if not events:
                continue
            # Stream events pass through downstream hooks; dict responses (TypedDicts)
            # skip stream_event since they're a different emission type.
            remaining = self._sub_policies[i + 1 :]
            for downstream in remaining:
                next_events: list[AnthropicPolicyEmission] = []
                for evt in events:
                    if not isinstance(evt, dict):
                        next_events.extend(
                            await downstream.on_anthropic_stream_event(evt, context)  # type: ignore[attr-defined]
                        )
                    else:
                        next_events.append(cast("AnthropicPolicyEmission", evt))
                events = next_events
                if not events:
                    break
            all_events.extend(events)
        return all_events

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Delegate Anthropic cleanup helper hook to sub-policies when present."""
        for policy in self._sub_policies:
            maybe_hook = getattr(policy, "on_anthropic_streaming_policy_complete", None)
            if maybe_hook is not None:
                await maybe_hook(context)


__all__ = ["MultiSerialPolicy"]
