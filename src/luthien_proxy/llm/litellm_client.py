# ABOUTME: LiteLLMClient implementation using litellm library
# ABOUTME: Provides stream() and complete() methods wrapping litellm.acompletion

"""LiteLLM client implementation."""

from collections.abc import AsyncIterator
from typing import cast

import litellm
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.client import LLMClient
from luthien_proxy.messages import Request

# Allow LiteLLM to pass through unknown models without validation
# This is needed for new OpenAI models that LiteLLM doesn't recognize yet
litellm.drop_params = True

# Track which models we've already registered
_registered_models: set[str] = set()


def _ensure_model_registered(model: str) -> None:
    """Register unknown OpenAI models with LiteLLM to bypass validation.

    LiteLLM validates models against its known list, but OpenAI releases
    new models frequently. This dynamically registers unknown models.
    """
    if model in _registered_models:
        return

    # Only register models that look like OpenAI models
    openai_prefixes = ("gpt-", "o1-", "o3-", "chatgpt-", "davinci", "babbage", "ada", "curie")
    base_model = model.split("/")[-1]  # Handle openai/gpt-4 format

    if not base_model.startswith(openai_prefixes):
        return

    # Check if model is already known to LiteLLM
    try:
        litellm.get_model_info(model)
        _registered_models.add(model)
        return
    except Exception:
        pass

    # Register the unknown model with default OpenAI settings
    litellm.register_model(
        {
            model: {
                "max_tokens": 128000,
                "max_input_tokens": 128000,
                "max_output_tokens": 16384,
                "input_cost_per_token": 0.0,
                "output_cost_per_token": 0.0,
                "litellm_provider": "openai",
                "mode": "chat",
            }
        }
    )
    _registered_models.add(model)


def _normalize_model_name(model: str) -> str:
    """Normalize model name to include provider prefix for LiteLLM.

    LiteLLM requires provider prefixes for models it doesn't recognize.
    This function adds 'openai/' prefix to GPT models that don't already
    have a provider prefix.
    """
    # If already has a provider prefix, return as-is
    if "/" in model:
        return model

    # OpenAI model patterns that should get the openai/ prefix
    openai_prefixes = ("gpt-", "o1-", "o3-", "chatgpt-", "davinci", "babbage", "ada", "curie")
    if model.startswith(openai_prefixes):
        return f"openai/{model}"

    return model


class LiteLLMClient(LLMClient):
    """LLM client using litellm library."""

    async def stream(self, request: Request) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        if "model" in data:
            data["model"] = _normalize_model_name(data["model"])
            _ensure_model_registered(data["model"])
        response_stream = await litellm.acompletion(**data)
        # litellm returns AsyncIterator when stream=True
        return cast(AsyncIterator[ModelResponse], response_stream)

    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        if "model" in data:
            data["model"] = _normalize_model_name(data["model"])
            _ensure_model_registered(data["model"])
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)


__all__ = ["LiteLLMClient"]
