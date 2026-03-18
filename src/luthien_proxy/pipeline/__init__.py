"""Unified request processing pipeline.

This module provides processing pipelines for LLM requests:
- process_anthropic_request: Anthropic-native path for processing requests

Provides structured span hierarchy for observability.
"""

from luthien_proxy.pipeline.anthropic_processor import process_anthropic_request
from luthien_proxy.pipeline.client_format import ClientFormat

__all__ = ["ClientFormat", "process_anthropic_request"]
