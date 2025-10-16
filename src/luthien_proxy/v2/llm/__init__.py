# ABOUTME: LLM integration module - LiteLLM wrapper and format converters
# ABOUTME: Handles multi-provider LLM calls and format normalization

"""LLM integration using LiteLLM as a library."""

from .format_converters import (
    anthropic_to_openai_request,
    openai_chunk_to_anthropic_chunk,
    openai_to_anthropic_response,
)

__all__ = [
    "anthropic_to_openai_request",
    "openai_to_anthropic_response",
    "openai_chunk_to_anthropic_chunk",
]
