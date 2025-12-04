"""No-op policy using SimplePolicy base for testing buffered streaming."""

from __future__ import annotations

from luthien_proxy.policies.simple_policy import SimplePolicy


class SimpleNoOpPolicy(SimplePolicy):
    """No-op policy using SimplePolicy base.

    This policy buffers streaming content (due to SimplePolicy's design) but applies
    no transformations. Useful for testing streaming reconstruction without policy logic,
    and as an MVP example of extending SimplePolicy.
    """

    pass


__all__ = ["SimpleNoOpPolicy"]
