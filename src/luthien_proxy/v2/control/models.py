# ABOUTME: Data models for control plane interface - requests, results, streaming context
# ABOUTME: Protocol-agnostic models that work with both local and networked implementations

"""Data models for control plane interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


class StreamAction(Enum):
    """Actions that can be returned from streaming policies."""

    CONTINUE = "continue"
    ABORT = "abort"
    SWITCH_MODEL = "switch_model"


@dataclass
class RequestMetadata:
    """Metadata about the request context.

    This is passed alongside the request data to give the control plane
    context about who is making the request, when, and how to track it.
    """

    call_id: str
    timestamp: datetime
    api_key_hash: str
    trace_id: Optional[str] = None
    user_id: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "call_id": self.call_id,
            "timestamp": self.timestamp.isoformat(),
            "api_key_hash": self.api_key_hash,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequestMetadata:
        """Create from dictionary."""
        return cls(
            call_id=data["call_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            api_key_hash=data["api_key_hash"],
            trace_id=data.get("trace_id"),
            user_id=data.get("user_id"),
            extra=data.get("extra", {}),
        )


@dataclass
class PolicyResult(Generic[T]):
    """Result of policy application.

    Attributes:
        value: The transformed/validated value
        allowed: Whether the operation is allowed by policy
        reason: Optional explanation of the decision
        metadata: Additional metadata from policy execution
    """

    value: T
    allowed: bool = True
    reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "value": self.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], value_type: type[T]) -> PolicyResult[T]:
        """Create from dictionary."""
        return cls(
            value=value_type(**data["value"]) if isinstance(data["value"], dict) else data["value"],
            allowed=data.get("allowed", True),
            reason=data.get("reason"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class StreamingContext:
    """Context for streaming operations.

    This is created at the start of a stream and passed to each chunk handler.
    It maintains state across chunks for a single streaming request.
    """

    stream_id: str
    call_id: str
    request_data: dict[str, Any]
    policy_state: dict[str, Any] = field(default_factory=dict)
    chunk_count: int = 0
    should_abort: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "stream_id": self.stream_id,
            "call_id": self.call_id,
            "request_data": self.request_data,
            "policy_state": self.policy_state,
            "chunk_count": self.chunk_count,
            "should_abort": self.should_abort,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamingContext:
        """Create from dictionary."""
        return cls(
            stream_id=data["stream_id"],
            call_id=data["call_id"],
            request_data=data["request_data"],
            policy_state=data.get("policy_state", {}),
            chunk_count=data.get("chunk_count", 0),
            should_abort=data.get("should_abort", False),
        )


__all__ = [
    "RequestMetadata",
    "PolicyResult",
    "StreamingContext",
    "StreamAction",
]
