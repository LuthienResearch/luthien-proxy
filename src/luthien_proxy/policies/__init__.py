"""Policies package initialization.

All policies implement both OpenAIPolicyInterface and AnthropicPolicyInterface,
supporting both API formats through a unified implementation.
"""

from luthien_proxy.policies.all_caps_policy import AllCapsPolicy
from luthien_proxy.policies.debug_logging_policy import DebugLoggingPolicy
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policies.parallel_rules_policy import ParallelRulesPolicy
from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policies.string_replacement_policy import StringReplacementPolicy
from luthien_proxy.policies.tool_call_judge_policy import ToolCallJudgePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol

__all__ = [
    # Legacy protocol (for backwards compatibility)
    "PolicyProtocol",
    # Context
    "PolicyContext",
    # Policies
    "AllCapsPolicy",
    "DebugLoggingPolicy",
    "NoOpPolicy",
    "ParallelRulesPolicy",
    "SimpleJudgePolicy",
    "SimplePolicy",
    "StringReplacementPolicy",
    "ToolCallJudgePolicy",
]
