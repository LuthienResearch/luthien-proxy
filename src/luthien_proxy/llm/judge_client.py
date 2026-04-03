"""Judge LLM client wrapper.

Translates Credential objects to LiteLLM kwargs so the rest of the system
interacts with typed credentials, not raw strings + auth_type flags.
"""

from __future__ import annotations

from typing import Any, cast

from litellm import acompletion
from litellm.types.utils import Choices, ModelResponse

from luthien_proxy.credentials.credential import Credential, CredentialType


async def judge_completion(
    credential: Credential,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 256,
    api_base: str | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    """Make a judge LLM call using the given credential.

    Translates Credential -> LiteLLM kwargs internally.
    Returns the response content string.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if api_base:
        kwargs["api_base"] = api_base
    if response_format:
        kwargs["response_format"] = response_format

    if credential.credential_type == CredentialType.AUTH_TOKEN:
        kwargs["extra_headers"] = {"Authorization": f"Bearer {credential.value}"}
        # LiteLLM needs a non-None api_key even for bearer auth
        kwargs["api_key"] = "placeholder"
    else:
        kwargs["api_key"] = credential.value

    response = await acompletion(**kwargs)
    response = cast(ModelResponse, response)
    if not response.choices:
        raise ValueError("LLM returned no choices")
    content = cast(Choices, response.choices[0]).message.content
    if content is None:
        raise ValueError("LLM response content is None")
    return cast(str, content)
