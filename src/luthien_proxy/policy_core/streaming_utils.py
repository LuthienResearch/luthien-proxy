# ABOUTME: Helper functions for policies to manipulate streaming responses
# ABOUTME: Provides send_text, send_chunk, passthrough functions for easy policy authoring

"""Module docstring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from luthien_proxy.policy_core.chunk_builders import create_text_chunk, create_tool_call_chunk

if TYPE_CHECKING:
    from litellm.types.utils import ChatCompletionMessageToolCall as ToolCall
    from litellm.types.utils import ModelResponse

    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )


async def send_text(ctx: StreamingPolicyContext, text: str) -> None:
    """Send text chunk to egress."""
    if not text:
        raise ValueError("text must be non-empty")

    chunk = create_text_chunk(text)
    await ctx.egress_queue.put(chunk)


async def send_chunk(ctx: StreamingPolicyContext, chunk: ModelResponse) -> None:
    """Send chunk to egress."""
    await ctx.egress_queue.put(chunk)


def get_last_ingress_chunk(ctx: StreamingPolicyContext) -> ModelResponse | None:
    """Get most recent ingress chunk."""
    chunks = ctx.original_streaming_response_state.raw_chunks
    return chunks[-1] if chunks else None


async def passthrough_last_chunk(ctx: StreamingPolicyContext) -> None:
    """Passthrough most recent ingress chunk to egress."""
    chunk = get_last_ingress_chunk(ctx)
    if chunk:
        await send_chunk(ctx, chunk)


async def passthrough_accumulated_chunks(ctx: StreamingPolicyContext) -> None:
    """Emit all chunks buffered since last emission.

    Preserves original chunk timing when content unchanged.
    """
    start_idx = ctx.original_streaming_response_state.last_emission_index
    chunks = ctx.original_streaming_response_state.raw_chunks[start_idx:]

    for chunk in chunks:
        await send_chunk(ctx, chunk)

    ctx.original_streaming_response_state.last_emission_index = len(ctx.original_streaming_response_state.raw_chunks)


async def send_tool_call(ctx: StreamingPolicyContext, tool_call: ToolCall) -> None:
    """Send complete tool call as chunk."""
    chunk = create_tool_call_chunk(tool_call)
    await ctx.egress_queue.put(chunk)


__all__ = [
    "send_text",
    "send_chunk",
    "get_last_ingress_chunk",
    "passthrough_last_chunk",
    "passthrough_accumulated_chunks",
    "send_tool_call",
]
