# ABOUTME: StreamingPolicyContext provides context for policy invocations during streaming
# ABOUTME: Simplified context with only what policies need - no orchestrator coupling

"""Streaming policy context for policy hook invocations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.observability.context import ObservabilityContext
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.streaming.stream_state import StreamState


@dataclass
class StreamingPolicyContext:
    """Context for policy invocations during streaming.

    This is a simplified context that contains only what policies actually need,
    without orchestrator-specific fields. Policies use this to:
    - Inspect the current stream state (blocks, finish_reason, etc.)
    - Write chunks to egress_queue for client delivery
    - Access shared policy state via policy_ctx
    - Emit observability events
    - Call keepalive() during long-running operations to prevent timeout

    The original_streaming_response_state is a reference to the assembler's state
    object, which gets updated automatically as the assembler processes chunks.
    """

    policy_ctx: PolicyContext  # Contains transaction_id, scratchpad, request
    egress_queue: asyncio.Queue[ModelResponse]  # Where policies write chunks
    original_streaming_response_state: StreamState  # Assembler state (auto-updated)
    observability: ObservabilityContext  # For emitting events
    keepalive: Callable[[], None]  # Reset timeout during long-running operations


__all__ = ["StreamingPolicyContext"]
