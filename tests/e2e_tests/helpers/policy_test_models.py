"""ABOUTME: Data models for parameterized policy E2E tests.
ABOUTME: Defines request specs, response assertions, and test case structures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence


@dataclass(frozen=True)
class Message:
    """A single message in a conversation."""

    role: Literal["user", "assistant", "system"]
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class RequestSpec:
    """Specification for a single request in a conversation turn."""

    messages: Sequence[Message]
    tools: Sequence[dict[str, Any]] | None = None
    scenario: str | None = None
    extra_params: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResponseAssertion:
    """Expected response characteristics for validation."""

    should_contain_text: Sequence[str] | None = None
    should_not_contain_text: Sequence[str] | None = None
    should_have_tool_calls: bool | None = None
    finish_reason: str | None = None
    debug_type: str | None = None
    debug_payload_assertions: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConversationTurn:
    """A single turn in a multi-turn conversation."""

    request: RequestSpec
    expected_response: ResponseAssertion
    description: str = ""


@dataclass(frozen=True)
class PolicyTestCase:
    """Complete test case for a policy."""

    policy_config_path: str
    turns: Sequence[ConversationTurn]
    test_id: str
    description: str = ""


__all__ = [
    "Message",
    "RequestSpec",
    "ResponseAssertion",
    "ConversationTurn",
    "PolicyTestCase",
]
