"""Unified request processing pipeline.

This module provides processing pipelines for LLM requests:
- process_llm_request: OpenAI-native path with optional Anthropic format conversion
- process_anthropic_request: Anthropic-native path without format conversion

Both provide structured span hierarchy for observability.
"""

from luthien_proxy.pipeline.anthropic_processor import process_anthropic_request
from luthien_proxy.pipeline.client_format import ClientFormat
from luthien_proxy.pipeline.processor import process_llm_request

__all__ = ["ClientFormat", "process_llm_request", "process_anthropic_request"]
