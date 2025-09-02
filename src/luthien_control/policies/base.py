"""
Abstract base for Luthien Control Policies.

Policies mirror LiteLLM's CustomLogger hook signatures to keep mental models
simple. The Control Plane endpoints invoke these methods and return JSON
encodings of their results.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, Optional


class LuthienPolicy(ABC):
    """Mirror of LiteLLM hook API, executed server-side in the control plane."""

    @abstractmethod
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Optional[Dict[str, Any]],
        cache: Optional[Dict[str, Any]],
        data: Dict[str, Any],
        call_type: Optional[str],
    ) -> Optional[object]:
        """Return one of:
        - None: no change (pass-through)
        - str: a string response to short-circuit (reject/message)
        - dict: a modified request payload to send to the model
        """

    @abstractmethod
    async def async_post_call_success_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Optional[Dict[str, Any]],
        response: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Return a dict to replace the response, or None to keep original."""

    @abstractmethod
    async def streaming_on_chunk(
        self,
        user_api_key_dict: Optional[Dict[str, Any]],
        request_data: Dict[str, Any],
        chunk: Dict[str, Any],
        chunk_index: int,
        accumulated_text: str,
    ) -> Dict[str, Any]:
        """Decide per chunk.

        Return dict with keys:
        - action: "pass" | "suppress" | "edit" | "replace_stream"
        - chunk: dict (required for action=edit)
        """

    @abstractmethod
    async def streaming_replacement(
        self,
        request_data: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield OpenAI-style streaming chunks when replacing the stream."""
