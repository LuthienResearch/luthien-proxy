"""Shared policy contracts and utilities.

This module provides the neutral contract layer that decouples policies
from streaming. Both modules import from this policy_core layer to avoid
circular dependencies.

Policy Interfaces:
- BasePolicy: Base class providing common functionality
- OpenAIPolicyInterface: ABC for policies working with OpenAI-format types
- AnthropicPolicyInterface: ABC for policies working with Anthropic-native types

Legacy:
- PolicyProtocol: Legacy OpenAI-format protocol (use OpenAIPolicyInterface for new code)
"""

from luthien_proxy.policy_core.anthropic_interface import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
)
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.chunk_builders import (
    create_finish_chunk,
    create_text_chunk,
    create_text_response,
    create_tool_call_chunk,
)
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
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
    # ABC-based interfaces (preferred for new code)
    "BasePolicy",
    "OpenAIPolicyInterface",
    "AnthropicPolicyInterface",
    "AnthropicStreamEvent",
    # Legacy OpenAI protocol (still used by policy infrastructure)
    "PolicyProtocol",
    # Contexts
    "PolicyContext",
    "StreamingPolicyContext",
    # Chunk builders
    "create_finish_chunk",
    "create_text_chunk",
    "create_text_response",
    "create_tool_call_chunk",
    # Response utilities
    "extract_tool_calls_from_response",
    "chunk_contains_tool_call",
    "is_tool_call_complete",
]
