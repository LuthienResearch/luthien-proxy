"""Shared policy contracts and utilities.

This module provides the neutral contract layer that decouples policies
from streaming. Both modules import from this policy_core layer to avoid
circular dependencies.
"""

from luthien_proxy.policy_core.anthropic_protocol import (
    AnthropicPolicyProtocol,
    AnthropicStreamEvent,
)
from luthien_proxy.policy_core.chunk_builders import (
    create_finish_chunk,
    create_text_chunk,
    create_text_response,
    create_tool_call_chunk,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.policy_core.response_utils import (
    chunk_contains_tool_call,
    extract_tool_calls_from_response,
    is_tool_call_complete,
)
from luthien_proxy.policy_core.streaming_policy_context import (
    StreamingPolicyContext,
)

__all__ = [
    "AnthropicPolicyProtocol",
    "AnthropicStreamEvent",
    "PolicyContext",
    "PolicyProtocol",
    "StreamingPolicyContext",
    "create_finish_chunk",
    "create_text_chunk",
    "create_text_response",
    "create_tool_call_chunk",
    "extract_tool_calls_from_response",
    "chunk_contains_tool_call",
    "is_tool_call_complete",
]
