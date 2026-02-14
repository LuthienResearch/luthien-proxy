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
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Callable, TypeVar

from luthien_proxy.policies.multi_policy_utils import load_sub_policy
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
T = TypeVar("T")

VALID_STRATEGIES = frozenset({"first_block", "most_restrictive", "unanimous_pass", "majority_pass"})


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


class MultiParallelPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
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
    ) -> None:
        """Initialize with sub-policy configs and a consolidation strategy."""
        if consolidation_strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"Unknown consolidation_strategy '{consolidation_strategy}'. Valid options: {sorted(VALID_STRATEGIES)}"
            )

        self._sub_policies: list[PolicyProtocol] = [load_sub_policy(cfg) for cfg in policies]
        self._strategy = consolidation_strategy

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

    # =========================================================================
    # OpenAI Interface - Non-streaming
    # =========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Run all OpenAI-compatible sub-policies on the request in parallel."""
        openai_policies = [p for p in self._sub_policies if isinstance(p, OpenAIPolicyInterface)]
        if not openai_policies:
            return request

        request_copies = [request.model_copy(deep=True) for _ in openai_policies]
        context_copies = [copy.deepcopy(context) for _ in openai_policies]
        results = await asyncio.gather(
            *(
                p.on_openai_request(req_copy, ctx_copy)
                for p, req_copy, ctx_copy in zip(openai_policies, request_copies, context_copies)
            )
        )
        return self._consolidate_requests(request, results)

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Run all OpenAI-compatible sub-policies on the response in parallel."""
        openai_policies = [p for p in self._sub_policies if isinstance(p, OpenAIPolicyInterface)]
        if not openai_policies:
            return response

        response_copies = [copy.deepcopy(response) for _ in openai_policies]
        context_copies = [copy.deepcopy(context) for _ in openai_policies]
        results = await asyncio.gather(
            *(
                p.on_openai_response(resp_copy, ctx_copy)
                for p, resp_copy, ctx_copy in zip(openai_policies, response_copies, context_copies)
            )
        )
        return self._consolidate_openai_responses(response, results)

    def _consolidate(self, original: T, results: list[T], *, size_fn: Callable[[T], int]) -> T:
        """Pick the winning value based on the configured consolidation strategy."""
        modified = [r for r in results if r != original]
        if not modified:
            return original

        if self._strategy == "first_block":
            return modified[0]

        if self._strategy == "most_restrictive":
            return min(modified, key=size_fn)

        if self._strategy == "unanimous_pass":
            return modified[0]

        if self._strategy == "majority_pass":
            passed = len(results) - len(modified)
            if passed > len(results) / 2:
                return original
            return modified[0]

        raise AssertionError(f"Unsupported consolidation strategy: {self._strategy}")

    def _consolidate_requests(self, original: "Request", results: list["Request"]) -> "Request":
        """Pick the winning request based on the consolidation strategy.

        Modification detection uses != to check value equality (Pydantic model __eq__),
        not object identity. Since each policy receives a deep copy, a modified result
        will have different field values even if it's a different object reference.
        """
        return self._consolidate(original, results, size_fn=lambda r: len(str(r.messages)))

    def _consolidate_openai_responses(
        self, original: "ModelResponse", results: list["ModelResponse"]
    ) -> "ModelResponse":
        """Pick the winning OpenAI response based on the consolidation strategy.

        Modification detection uses != to check value equality (Pydantic model __eq__),
        not object identity. Since each policy receives a deep copy, a modified result
        will have different field values even if it's a different object reference.
        """
        return self._consolidate(original, results, size_fn=_response_content_length)

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
    # Anthropic Interface - Non-streaming
    # =========================================================================

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Run all Anthropic-compatible sub-policies on the request in parallel."""
        anthropic_policies = [p for p in self._sub_policies if isinstance(p, AnthropicPolicyInterface)]
        if not anthropic_policies:
            return request

        request_copies = [copy.deepcopy(request) for _ in anthropic_policies]
        context_copies = [copy.deepcopy(context) for _ in anthropic_policies]
        results = await asyncio.gather(
            *(
                p.on_anthropic_request(req_copy, ctx_copy)
                for p, req_copy, ctx_copy in zip(anthropic_policies, request_copies, context_copies)
            )
        )
        return self._consolidate_anthropic_requests(request, results)

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Run all Anthropic-compatible sub-policies on the response in parallel."""
        anthropic_policies = [p for p in self._sub_policies if isinstance(p, AnthropicPolicyInterface)]
        if not anthropic_policies:
            return response

        response_copies = [copy.deepcopy(response) for _ in anthropic_policies]
        context_copies = [copy.deepcopy(context) for _ in anthropic_policies]
        results = await asyncio.gather(
            *(
                p.on_anthropic_response(resp_copy, ctx_copy)
                for p, resp_copy, ctx_copy in zip(anthropic_policies, response_copies, context_copies)
            )
        )
        return self._consolidate_anthropic_responses(response, results)

    def _consolidate_anthropic_requests(
        self, original: "AnthropicRequest", results: list["AnthropicRequest"]
    ) -> "AnthropicRequest":
        """Pick the winning Anthropic request based on the consolidation strategy.

        Modification detection uses != to check value equality (dict __eq__),
        not object identity. Since each policy receives a deep copy, a modified result
        will have different field values even if it's a different object reference.
        """
        return self._consolidate(original, results, size_fn=lambda r: len(str(r.get("messages", []))))

    def _consolidate_anthropic_responses(
        self, original: "AnthropicResponse", results: list["AnthropicResponse"]
    ) -> "AnthropicResponse":
        """Pick the winning Anthropic response based on the consolidation strategy.

        Modification detection uses != to check value equality (dict __eq__),
        not object identity. Since each policy receives a deep copy, a modified result
        will have different field values even if it's a different object reference.
        """
        return self._consolidate(original, results, size_fn=_anthropic_response_content_length)

    # =========================================================================
    # Anthropic Interface - Streaming (not supported)
    # =========================================================================

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> list[AnthropicStreamEvent]:
        """Not supported -- raises NotImplementedError."""
        raise NotImplementedError(
            "MultiParallelPolicy does not support Anthropic streaming. "
            "Parallel policies need to see the complete response to consolidate results."
        )


__all__ = ["MultiParallelPolicy"]
