# ABOUTME: Anthropic stream executor that processes SDK streaming events through policy hooks
# ABOUTME: Sits between the Anthropic SDK stream and the client response formatter

"""Anthropic stream executor for processing streaming events through policies.

This module provides AnthropicStreamExecutor which takes an async iterator of
MessageStreamEvent from the Anthropic SDK, processes each event through a
policy's on_stream_event hook, and yields the events that should be sent to
the client.
"""

import logging
from collections.abc import AsyncIterator

from anthropic.lib.streaming import MessageStreamEvent
from opentelemetry import trace

from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class AnthropicStreamExecutor:
    """Executes policy processing on Anthropic streaming events.

    This executor sits between the Anthropic SDK stream and the client response
    formatter. It processes each streaming event through the policy's
    on_stream_event hook, which can:
    - Return the event unchanged (passthrough)
    - Return a modified event (transformation)
    - Return None to filter out the event

    The executor handles errors gracefully, logging them and continuing with
    the stream rather than crashing.
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
            span.set_attribute("policy.name", policy.short_policy_name)
            event_count = 0
            yielded_count = 0
            error_count = 0

            async for sdk_event in stream:
                event_count += 1

                try:
                    result = await policy.on_anthropic_stream_event(sdk_event, context)
                    if result is not None:
                        yielded_count += 1
                        yield result
                except Exception:
                    error_count += 1
                    logger.warning(
                        "Error in policy on_anthropic_stream_event for event type %s (error %d)",
                        getattr(sdk_event, "type", "unknown"),
                        error_count,
                        exc_info=True,
                    )
                    # Skip the event on error but continue processing

            span.set_attribute("streaming.event_count", event_count)
            span.set_attribute("streaming.yielded_count", yielded_count)
            span.set_attribute("streaming.error_count", error_count)


__all__ = ["AnthropicStreamExecutor"]
