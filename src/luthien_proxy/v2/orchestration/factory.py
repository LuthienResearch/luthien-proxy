# ABOUTME: Factory function for creating PolicyOrchestrator with default dependencies
# ABOUTME: Wires up observability context and transaction recorder factories

"""Module docstring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from luthien_proxy.v2.observability.context import (
    DefaultObservabilityContext,
    ObservabilityContext,
)
from luthien_proxy.v2.observability.transaction_recorder import (
    DefaultTransactionRecorder,
    TransactionRecorder,
)
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from luthien_proxy.utils.db import DatabasePool
    from luthien_proxy.v2.llm.client import LLMClient
    from luthien_proxy.v2.observability.redis_event_publisher import (
        RedisEventPublisher,
    )
    from luthien_proxy.v2.policies.policy import Policy


def create_default_orchestrator(
    policy: Policy,
    llm_client: LLMClient,
    db_pool: DatabasePool | None = None,
    event_publisher: RedisEventPublisher | None = None,
) -> PolicyOrchestrator:
    """Create orchestrator with default dependencies."""

    def observability_factory(transaction_id: str, span: Span) -> ObservabilityContext:
        return DefaultObservabilityContext(
            transaction_id=transaction_id,
            span=span,
            db_pool=db_pool,
            event_publisher=event_publisher,
        )

    def recorder_factory(observability: ObservabilityContext) -> TransactionRecorder:
        return DefaultTransactionRecorder(observability=observability)

    return PolicyOrchestrator(
        policy=policy,
        llm_client=llm_client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
        streaming_orchestrator=None,
    )


__all__ = ["create_default_orchestrator"]
