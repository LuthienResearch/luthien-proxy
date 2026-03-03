"""General-purpose policy composition.

Provides compose_policy() for inserting policies into an existing policy chain
at runtime. Works with both single policies and existing MultiSerialPolicy chains.
"""

from __future__ import annotations

import logging

from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policy_core import PolicyProtocol

logger = logging.getLogger(__name__)


def compose_policy(
    current: PolicyProtocol,
    additional: PolicyProtocol,
    position: int | None = None,
) -> MultiSerialPolicy:
    """Insert a policy into the current policy chain.

    If current is already a MultiSerialPolicy, inserts into its existing chain.
    Otherwise, wraps both policies into a new MultiSerialPolicy.

    Args:
        current: The existing active policy.
        additional: The policy to add to the chain.
        position: Where to insert. None (default) appends to end.
                  0 inserts at the beginning. Uses list.insert() semantics
                  for other values.

    Returns:
        A MultiSerialPolicy containing both policies.
    """
    if isinstance(current, MultiSerialPolicy):
        policies = list(current._sub_policies)
    else:
        policies = [current]

    if position is None:
        policies.append(additional)
    else:
        policies.insert(position, additional)

    result = MultiSerialPolicy.from_instances(policies)
    logger.info(
        f"Composed policy chain: inserted {additional.short_policy_name} "
        f"at position {position} → {result.short_policy_name}"
    )
    return result


__all__ = ["compose_policy"]
