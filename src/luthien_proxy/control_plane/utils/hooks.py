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

    Why: LiteLLM emits slightly different shapes across hooks. Keeping a
    single, table‑driven extractor avoids drift and long if/elif chains.
    """
    hook = hook.lower()
    hook_to_id_path = {
        "async_pre_call_hook": ["data", "litellm_call_id"],
        "async_post_call_success_hook": ["data", "litellm_call_id"],
        "async_post_call_streaming_iterator_hook": ["request_data", "litellm_call_id"],
    }
    if hook not in hook_to_id_path:
        logging.error(f"No litellm_call_id path defined for hook '{hook}'")
        return None
    path = hook_to_id_path[hook]
    litellm_call_id_value = _get_in(payload, path)
    if litellm_call_id_value is None:
        logging.error(f"Could not find litellm_call_id at path {path} in payload")
        return None
    if not isinstance(litellm_call_id_value, str):
        logging.error(
            f"litellm_call_id at path {path} is not a string: {litellm_call_id_value}",
        )
        return None
    return litellm_call_id_value
