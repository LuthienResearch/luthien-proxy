"""Unified request processing pipeline.

This module provides a unified processing pipeline for LLM requests,
abstracting over client format differences (OpenAI vs Anthropic) and
providing structured span hierarchy for observability.
"""

from luthien_proxy.pipeline.client_format import ClientFormat
from luthien_proxy.pipeline.processor import process_llm_request

__all__ = ["ClientFormat", "process_llm_request"]
