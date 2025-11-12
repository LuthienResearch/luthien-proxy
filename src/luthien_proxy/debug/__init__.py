# ABOUTME: Debug module for V2 gateway - query endpoints for conversation events
# ABOUTME: Provides REST API to retrieve and diff policy decisions

"""Debug module for V2 gateway.

This module provides REST endpoints for debugging policy decisions:
- Retrieve conversation events by call_id
- Compute diffs between original and final requests/responses
- List recent calls with filtering
"""

from luthien_proxy.debug.routes import router

__all__ = ["router"]
