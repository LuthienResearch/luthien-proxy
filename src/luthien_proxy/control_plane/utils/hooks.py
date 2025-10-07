"""Helpers to extract a stable litellm_call_id from varied hook payloads."""

from __future__ import annotations

import logging
from typing import Optional, cast

from luthien_proxy.types import JSONObject, JSONValue


def _get_in(d: JSONObject, path: list[str]) -> Optional[JSONValue]:
    cur: JSONValue = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cast(JSONValue, cur.get(k))
    return cur


def extract_call_id_for_hook(hook: str, payload: JSONObject) -> Optional[str]:
    """Return the `litellm_call_id` for a given hook payload.

    Why: LiteLLM emits different payload shapes based on hook type AND call type.

    Call ID location varies by:
    1. Hook type (pre_call vs post_call vs streaming)
    2. Call type (acompletion vs text_completion vs streaming)
    3. Payload structure (data vs request_data keys)

    Observed patterns:
    - async_pre_call_hook + call_type=acompletion: data.litellm_call_id ✓
    - async_pre_call_hook + call_type=text_completion: NO call_id (generated later)
    - async_post_call_success_hook: data.litellm_metadata.model_info.id ✓
    - async_post_call_streaming_iterator_hook: request_data.litellm_metadata.model_info.id ✓

    We check multiple paths as fallbacks to handle all cases.
    """
    hook = hook.lower()
    hook_to_id_paths: dict[str, list[list[str]]] = {
        "async_pre_call_hook": [
            ["data", "litellm_call_id"],  # Present for call_type=acompletion, absent for text_completion
        ],
        "async_post_call_success_hook": [
            ["data", "litellm_metadata", "model_info", "id"],
            ["data", "litellm_call_id"],  # Fallback/legacy
        ],
        "async_post_call_streaming_iterator_hook": [
            ["request_data", "litellm_metadata", "model_info", "id"],
            ["request_data", "litellm_call_id"],  # Fallback/legacy
        ],
        "async_post_call_streaming_hook": [
            ["data", "litellm_metadata", "model_info", "id"],
            ["data", "litellm_call_id"],  # Fallback/legacy
        ],
        "async_post_call_failure_hook": [
            ["request_data", "litellm_metadata", "model_info", "id"],
            ["request_data", "litellm_call_id"],  # Fallback/legacy
        ],
    }
    paths = hook_to_id_paths.get(hook)
    if paths is None:
        logging.warning(f"No litellm_call_id path defined for hook '{hook}'")
        return None

    if not paths:
        # Empty list means call_id not available for this hook
        return None

    for path in paths:
        litellm_call_id_value = _get_in(payload, path)
        if litellm_call_id_value is None:
            continue
        if not isinstance(litellm_call_id_value, str):
            logging.error(
                f"litellm_call_id at path {path} is not a string: {litellm_call_id_value}",
            )
            return None
        return litellm_call_id_value

    logging.warning(f"Could not find litellm_call_id at any known path for hook '{hook}'")
    return None
