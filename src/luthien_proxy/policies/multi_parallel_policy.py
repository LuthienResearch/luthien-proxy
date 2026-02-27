"""MultiParallelPolicy - Run multiple policies in parallel and consolidate results.

All sub-policies see the same original request/response. A configurable
consolidation strategy decides which result "wins" when policies disagree.

Streaming is not supported for parallel policies -- the parallel execution
model requires each policy to see the complete response. Streaming hooks
raise NotImplementedError with a clear message.

Example config:
    policy:
      class: "luthien_proxy.policies.multi_parallel_policy:MultiParallelPolicy"
      config:
        consolidation_strategy: "first_block"
        policies:
          - class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
            config: { model: "openai/gpt-4o-mini" }
          - class: "luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy"
            config: {}

    # "designated" strategy example - always use the second policy's result:
    policy:
      class: "luthien_proxy.policies.multi_parallel_policy:MultiParallelPolicy"
      config:
        consolidation_strategy: "designated"
        designated_policy_index: 1
        policies:
          - class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
            config: {}
          - class: "luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy"
            config: {}
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Callable, TypeVar, cast

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
T = TypeVar("T")

VALID_STRATEGIES = frozenset({"first_block", "most_restrictive", "unanimous_pass", "majority_pass", "designated"})


def _response_content_length(response: "ModelResponse") -> int:
    """Rough measure of response "size" for most_restrictive comparison."""
    total = 0
    for choice in response.choices:
        msg = getattr(choice, "message", None)
        if msg and isinstance(msg.content, str):
            total += len(msg.content)
    return total


def _anthropic_response_content_length(response: "AnthropicResponse") -> int:
    """Rough measure of Anthropic response "size" for most_restrictive comparison."""
    total = 0
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                total += len(text)
    return total


class MultiParallelPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Run multiple policies in parallel and consolidate their results.

    Each sub-policy receives an independent copy of the original input.
    A consolidation strategy decides the final output:

    - "first_block": If any policy modifies the response, use the first
      modified version. If none modify it, pass through the original.
    - "most_restrictive": Among policies that modified the response, pick
      the shortest (most restricted) output.
    - "unanimous_pass": The response passes unchanged only if ALL policies
      leave it unchanged. If any policy modifies it, use the first modified version.
    - "majority_pass": The response passes unchanged if a strict majority
      of policies leave it unchanged. Otherwise use the first modified version.
    - "designated": Use the result from the policy at designated_policy_index.
      If that policy didn't modify the response, return the original unchanged.

    Context isolation: Each sub-policy receives a deep copy of the context.
    Any mutations made to context by sub-policies are discarded after parallel
    execution completes. Context modifications are not propagated back.

    Error handling: If any sub-policy raises an exception during parallel execution
    (asyncio.gather without return_exceptions=True), the exception propagates
    immediately and fails the entire request/response processing for all policies.
    """

    def __init__(
        self,
        policies: list[dict[str, Any]],
        consolidation_strategy: str = "first_block",
        designated_policy_index: int | None = None,
    ) -> None:
        """Initialize with sub-policy configs and a consolidation strategy."""
        if consolidation_strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"Unknown consolidation_strategy '{consolidation_strategy}'. Valid options: {sorted(VALID_STRATEGIES)}"
            )

        self._sub_policies: list[PolicyProtocol] = [load_sub_policy(cfg) for cfg in policies]
        self._validated_interfaces: set[type] = set()
        if not self._sub_policies:
            # Warning (not error) because an empty list is a valid degenerate case:
            # the multi-policy becomes a no-op passthrough, which is safe and predictable.
            logger.warning(
                "MultiParallelPolicy initialized with empty policy list — requests will pass through unchanged"
            )
        self._strategy = consolidation_strategy

        if consolidation_strategy == "designated":
            if designated_policy_index is None:
                raise ValueError("designated_policy_index is required when using the 'designated' strategy")
            if not (0 <= designated_policy_index < len(self._sub_policies)):
                raise ValueError(
                    f"designated_policy_index {designated_policy_index} is out of range "
                    f"(have {len(self._sub_policies)} policies)"
                )
        self._designated_policy_index = designated_policy_index

        names = [p.short_policy_name for p in self._sub_policies]
        logger.info(
            f"MultiParallelPolicy initialized with strategy='{consolidation_strategy}', "
            f"{len(self._sub_policies)} policies: {names}"
        )

    @property
    def short_policy_name(self) -> str:
        """Human-readable name showing strategy and sub-policy composition."""
        names = [p.short_policy_name for p in self._sub_policies]
        return f"MultiParallel[{self._strategy}]({', '.join(names)})"

    def _validate_interface(self, interface: type, interface_name: str) -> None:
        """Raise TypeError if any sub-policy doesn't implement the required interface."""
        validate_sub_policies_interface(
            self._sub_policies, self._validated_interfaces, interface, interface_name, "MultiParallelPolicy"
        )

    # =========================================================================
    # OpenAI Interface - Non-streaming
    # =========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Run all sub-policies on the request in parallel."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        if not self._sub_policies:
            return request

        request_copies = [request.model_copy(deep=True) for _ in self._sub_policies]
        # Deep copying contexts is O(context size) per policy — may be expensive for
        # large conversation histories. Necessary to guarantee context isolation.
        context_copies = [copy.deepcopy(context) for _ in self._sub_policies]
        results = await asyncio.gather(
            *(
                p.on_openai_request(req_copy, ctx_copy)  # type: ignore[union-attr]
                for p, req_copy, ctx_copy in zip(self._sub_policies, request_copies, context_copies)
            )
        )
        return self._consolidate(request, list(results), size_fn=lambda r: len(str(r.messages)))

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Run all sub-policies on the response in parallel."""
        self._validate_interface(OpenAIPolicyInterface, "OpenAIPolicyInterface")
        if not self._sub_policies:
            return response

        # Deep copying responses and contexts is O(size) per policy — may be expensive
        # for large payloads. Necessary to guarantee each policy sees independent state.
        response_copies = [copy.deepcopy(response) for _ in self._sub_policies]
        context_copies = [copy.deepcopy(context) for _ in self._sub_policies]
        results = await asyncio.gather(
            *(
                p.on_openai_response(resp_copy, ctx_copy)  # type: ignore[union-attr]
                for p, resp_copy, ctx_copy in zip(self._sub_policies, response_copies, context_copies)
            )
        )
        return self._consolidate(response, list(results), size_fn=_response_content_length)

    def _consolidate(
        self,
        original: T,
        results: list[T],
        *,
        size_fn: Callable[[T], int],
    ) -> T:
        """Pick the winning value based on the configured consolidation strategy."""
        if self._strategy == "designated":
            designated_index = cast(int, self._designated_policy_index)
            designated = results[designated_index]
            return designated if designated != original else original

        modified = [r for r in results if r != original]
        if not modified:
            return original

        if self._strategy == "majority_pass":
            passed = len(results) - len(modified)
            if passed > len(results) / 2:
                return original

        if self._strategy == "most_restrictive":
            return min(modified, key=size_fn)

        # "unanimous_pass" and "first_block" share this code path intentionally.
        # Both return modified[0] when any policy modifies the result. They're
        # semantic aliases for different mental models (see class docstring):
        # "first_block" emphasizes "use the first modification", while
        # "unanimous_pass" emphasizes "only pass if ALL agree to pass".
        return modified[0]

    # =========================================================================
    # OpenAI Interface - Streaming (not supported)
    # =========================================================================

    def _streaming_not_supported(self) -> None:
        raise NotImplementedError(
            "MultiParallelPolicy does not support streaming. "
            "Parallel policies need to see the complete response to consolidate results. "
            "Use non-streaming mode, or wrap MultiParallelPolicy inside a MultiSerialPolicy "
            "with a buffering policy."
        )

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Not supported -- raises NotImplementedError."""
        self._streaming_not_supported()

    # =========================================================================
    # Anthropic execution interface
    # =========================================================================

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Run Anthropic execution policies in parallel and emit the winning output."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            self._validate_interface(AnthropicExecutionInterface, "AnthropicExecutionInterface")
            execution_policies = [
                policy for policy in self._sub_policies if isinstance(policy, AnthropicExecutionInterface)
            ]

            if not execution_policies:
                request = io.request
                if request.get("stream", False):
                    async for event in io.stream(request):
                        yield event
                    return
                yield await io.complete(request)
                return

            request = io.request
            if request.get("stream", False) and self._strategy != "designated":
                raise NotImplementedError(
                    "MultiParallelPolicy supports Anthropic streaming only with consolidation_strategy='designated'."
                )

            if self._strategy == "designated":
                designated_idx = cast(int, self._designated_policy_index)
                designated = execution_policies[designated_idx]
                designated_io = _ParallelAnthropicIO(
                    initial_request=copy.deepcopy(request),
                    terminal_io=io,
                )
                designated_ctx = copy.deepcopy(context)
                async for emitted in designated.run_anthropic(designated_io, designated_ctx):
                    yield emitted
                return

            policy_ios = [
                _ParallelAnthropicIO(initial_request=copy.deepcopy(request), terminal_io=io) for _ in execution_policies
            ]
            context_copies = [copy.deepcopy(context) for _ in execution_policies]

            emissions_per_policy = await asyncio.gather(
                *(
                    _collect_policy_emissions(policy, policy_io, ctx_copy)
                    for policy, policy_io, ctx_copy in zip(execution_policies, policy_ios, context_copies)
                )
            )

            winner_idx = self._select_winner_index(emissions_per_policy)
            winner_emissions = emissions_per_policy[winner_idx]

            for emitted in winner_emissions:
                yield emitted

        return _run()

    def _select_winner_index(self, emissions_per_policy: list[list[AnthropicPolicyEmission]]) -> int:
        """Choose the winning policy output transcript by configured strategy."""
        if not emissions_per_policy:
            return 0

        if self._strategy in {"first_block", "unanimous_pass"}:
            return 0

        if self._strategy == "most_restrictive":
            lengths = [_emission_transcript_length(emissions) for emissions in emissions_per_policy]
            return min(range(len(lengths)), key=lengths.__getitem__)

        if self._strategy == "majority_pass":
            winner_idx = 0
            winner_count = 0
            for idx, emissions in enumerate(emissions_per_policy):
                count = sum(1 for other in emissions_per_policy if other == emissions)
                if count > winner_count:
                    winner_idx = idx
                    winner_count = count
            return winner_idx

        # designated is handled in run_anthropic before collection.
        return 0

    # =========================================================================
    # Anthropic helper hooks (for policy-level unit tests)
    # =========================================================================

    async def on_anthropic_request(self, request: AnthropicRequest, context: "PolicyContext") -> AnthropicRequest:
        """Run Anthropic request helpers in parallel and consolidate results."""
        self._validate_interface(AnthropicExecutionInterface, "AnthropicExecutionInterface")
        if not self._sub_policies:
            return request

        request_copies = [copy.deepcopy(request) for _ in self._sub_policies]
        context_copies = [copy.deepcopy(context) for _ in self._sub_policies]
        results = await asyncio.gather(
            *(
                p.on_anthropic_request(req_copy, ctx_copy)  # type: ignore[attr-defined]
                for p, req_copy, ctx_copy in zip(self._sub_policies, request_copies, context_copies)
            )
        )
        return self._consolidate(request, list(results), size_fn=lambda r: len(str(r.get("messages", []))))

    async def on_anthropic_response(self, response: AnthropicResponse, context: "PolicyContext") -> AnthropicResponse:
        """Run Anthropic response helpers in parallel and consolidate results."""
        self._validate_interface(AnthropicExecutionInterface, "AnthropicExecutionInterface")
        if not self._sub_policies:
            return response

        response_copies = [copy.deepcopy(response) for _ in self._sub_policies]
        context_copies = [copy.deepcopy(context) for _ in self._sub_policies]
        results = await asyncio.gather(
            *(
                p.on_anthropic_response(resp_copy, ctx_copy)  # type: ignore[attr-defined]
                for p, resp_copy, ctx_copy in zip(self._sub_policies, response_copies, context_copies)
            )
        )
        return self._consolidate(response, list(results), size_fn=_anthropic_response_content_length)

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Anthropic stream-event helper remains unsupported in parallel mode."""
        raise NotImplementedError(
            "MultiParallelPolicy does not support Anthropic streaming helper hooks. "
            "Use run_anthropic for execution-oriented streaming behavior."
        )


class _ParallelAnthropicIO(AnthropicPolicyIOProtocol):
    """Per-policy IO wrapper for parallel Anthropic execution."""

    def __init__(self, *, initial_request: AnthropicRequest, terminal_io: AnthropicPolicyIOProtocol) -> None:
        self._request = initial_request
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
        return await self._terminal_io.complete(request or self._request)

    def stream(self, request: AnthropicRequest | None = None) -> AsyncIterator[MessageStreamEvent]:
        return self._terminal_io.stream(request or self._request)


async def _collect_policy_emissions(
    policy: AnthropicExecutionInterface,
    io: AnthropicPolicyIOProtocol,
    context: PolicyContext,
) -> list[AnthropicPolicyEmission]:
    """Collect all emissions from a single execution policy."""
    emissions: list[AnthropicPolicyEmission] = []
    async for emitted in policy.run_anthropic(io, context):
        emissions.append(emitted)
    return emissions


def _emission_transcript_length(emissions: list[AnthropicPolicyEmission]) -> int:
    """Approximate transcript length for most_restrictive consolidation."""
    total = 0
    for emitted in emissions:
        if isinstance(emitted, dict) and emitted.get("type") == "message":
            total += _anthropic_response_content_length(emitted)
        else:
            total += len(str(emitted))
    return total


__all__ = ["MultiParallelPolicy"]
