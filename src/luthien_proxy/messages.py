"""Re-export Request type for backwards compatibility.

NOTE: This module is deprecated. Import Request from luthien_proxy.llm.types instead.
"""

from luthien_proxy.llm.types import Request

__all__ = ["Request"]
