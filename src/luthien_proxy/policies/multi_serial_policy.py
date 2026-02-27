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
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, cast

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
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


class MultiSerialPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Run multiple policies sequentially, piping each output to the next.

    For requests: request flows through policy1 -> policy2 -> ... -> policyN
    For responses: response flows through policy1 -> policy2 -> ... -> policyN

    For Anthropic execution, each policy sees the next policy as its backend.
    All sub-policies must implement the interface being called.
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
        """Execute sub-policies serially by treating policy N+1 as policy N's backend."""
        execution_policies = self._iter_execution_policies()

        if not execution_policies:
            return self._passthrough_anthropic(io)

        def _execute_from(index: int, request: AnthropicRequest) -> AsyncIterator[AnthropicPolicyEmission]:
            if index >= len(execution_policies):
                return self._call_terminal_backend(io, request)

            downstream = _SerialChainedAnthropicIO(
                initial_request=request,
                execute_next=lambda req: _execute_from(index + 1, req),
                terminal_io=io,
            )
            return execution_policies[index].run_anthropic(downstream, context)

        return _execute_from(0, io.request)

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

    def _call_terminal_backend(
        self, io: AnthropicPolicyIOProtocol, request: AnthropicRequest
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Call the actual backend when there are no more serial sub-policies."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
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


class _SerialChainedAnthropicIO(AnthropicPolicyIOProtocol):
    """Policy I/O adapter that routes backend calls into the next serial policy."""

    def __init__(
        self,
        *,
        initial_request: AnthropicRequest,
        execute_next: Callable[[AnthropicRequest], AsyncIterator[AnthropicPolicyEmission]],
        terminal_io: AnthropicPolicyIOProtocol,
    ) -> None:
        self._request = initial_request
        self._execute_next = execute_next
        self._terminal_io = terminal_io

    @property
    def request(self) -> AnthropicRequest:
        return self._request

    def set_request(self, request: AnthropicRequest) -> None:
        self._request = request

    @property
    def first_backend_response(self) -> AnthropicResponse | None:
        return self._terminal_io.first_backend_response

    async def complete(self, request: AnthropicRequest | None = None) -> AnthropicResponse:
        final_request = request or self._request
        response: AnthropicResponse | None = None

        async for emitted in self._execute_next(final_request):
            if isinstance(emitted, dict) and emitted.get("type") == "message":
                response = emitted
                continue
            raise TypeError("Downstream serial policy emitted streaming events during complete()")

        if response is None:
            raise RuntimeError("Downstream serial policy emitted no non-streaming response during complete()")
        return response

    def stream(self, request: AnthropicRequest | None = None) -> AsyncIterator[MessageStreamEvent]:
        final_request = request or self._request

        async def _stream() -> AsyncIterator[MessageStreamEvent]:
            async for emitted in self._execute_next(final_request):
                if isinstance(emitted, dict) and emitted.get("type") == "message":
                    raise TypeError("Downstream serial policy emitted a non-streaming response during stream()")
                yield cast(MessageStreamEvent, emitted)

        return _stream()


__all__ = ["MultiSerialPolicy"]
