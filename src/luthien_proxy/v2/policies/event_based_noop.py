# ABOUTME: V3 NoOp policy example - passes everything through unchanged
# ABOUTME: Demonstrates minimal EventBasedPolicy with all default implementations

"""V3 NoOp policy - passes all data through unchanged.

This is the simplest possible EventBasedPolicy implementation.
No hooks need to be overridden - the defaults handle everything.

Default behavior:
- on_request: returns request unchanged
- on_response: returns response unchanged
- on_content_delta: forwards each delta immediately
- on_tool_call_delta: forwards each delta immediately
- on_finish_reason: sends finish chunk
- All completion hooks: no-op (deltas already forwarded)
"""

from __future__ import annotations

from luthien_proxy.v2.streaming.event_based_policy import EventBasedPolicy


class EventBasedNoOpPolicy(EventBasedPolicy):
    """V3 NoOp policy - passes everything through unchanged.

    This policy demonstrates the simplest possible V3 implementation.
    No methods need to be overridden - the base class defaults
    provide complete pass-through behavior.

    Use this as:
    - A starting point for new policies
    - A performance baseline
    - A validation that the V3 system works end-to-end
    """

    # No overrides needed - defaults handle everything!
    pass


__all__ = ["EventBasedNoOpPolicy"]
