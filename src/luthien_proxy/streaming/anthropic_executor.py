# ABOUTME: Anthropic stream executor that processes SDK streaming events through policy hooks
# ABOUTME: Sits between the Anthropic SDK stream and the client response formatter

"""Anthropic stream executor for processing streaming events through policies.

This module provides AnthropicStreamExecutor which takes an async iterator of
MessageStreamEvent from the Anthropic SDK, processes each event through a
policy's on_stream_event hook, and yields the events that should be sent to
the client.
"""

from collections.abc import AsyncIterator

from anthropic.lib.streaming import MessageStreamEvent
from opentelemetry import trace

from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext

tracer = trace.get_tracer(__name__)


class AnthropicStreamExecutor:
    """Executes policy processing on Anthropic streaming events.

    This executor sits between the Anthropic SDK stream and the client response
    formatter. It processes each streaming event through the policy's
    on_stream_event hook, which can:
    - Return [event] to pass through unchanged
    - Return [modified_event] to transform
    - Return [] to filter out the event
    - Return [event1, event2, ...] to emit multiple events

    Policy errors propagate to the caller - if something fails, it fails loudly.
    """

    async def process(
        self,
        stream: AsyncIterator[MessageStreamEvent],
        policy: AnthropicPolicyInterface,
        context: PolicyContext,
    ) -> AsyncIterator[MessageStreamEvent]:
        """Process streaming events through the policy.

        Args:
            stream: Async iterator of MessageStreamEvent from Anthropic SDK
            policy: Policy implementing AnthropicPolicyInterface
            context: Policy context with scratchpad, emitter, etc.

        Yields:
            Events that should be sent to the client (filtered and/or transformed)
        """
        with tracer.start_as_current_span("anthropic.stream_executor") as span:
            span.set_attribute("policy.class", policy.__class__.__name__)
            # short_policy_name comes from BasePolicy, not the interface
            span.set_attribute("policy.name", getattr(policy, "short_policy_name", policy.__class__.__name__))
            event_count = 0
            yielded_count = 0

            async for sdk_event in stream:
                event_count += 1

                results = await policy.on_anthropic_stream_event(sdk_event, context)
                for result in results:
                    yielded_count += 1
                    yield result

            span.set_attribute("streaming.event_count", event_count)
            span.set_attribute("streaming.yielded_count", yielded_count)


__all__ = ["AnthropicStreamExecutor"]
