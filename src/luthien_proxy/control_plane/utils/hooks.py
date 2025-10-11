"""Helpers to extract luthien_call_id from hook payloads."""

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
    """Return the call_id for a given hook payload.

    We generate our own luthien_call_id in pre_call hooks and inject it into metadata
    for all providers/call types. Post-call hooks extract it from there.

    Extraction priority:
    1. data.metadata.luthien_call_id (our generated ID, always present for new calls)
    2. data.metadata.hidden_params.litellien_call_id (legacy non-streaming)
    3. Other LiteLLM paths (legacy fallbacks for old data)

    Note: async_pre_call_hook doesn't extract - we generate the ID instead.
    """
    hook = hook.lower()
    hook_to_id_paths: dict[str, list[list[str]]] = {
        "async_pre_call_hook": [
            # Pre-call hook doesn't extract - we generate the ID in hooks_routes.py
        ],
        "async_post_call_success_hook": [
            ["data", "metadata", "luthien_call_id"],  # Our generated ID (primary)
            ["data", "metadata", "hidden_params", "litellm_call_id"],  # Legacy non-streaming
            ["data", "litellm_metadata", "model_info", "id"],  # Legacy streaming (often wrong)
        ],
        "async_post_call_streaming_iterator_hook": [
            ["request_data", "metadata", "luthien_call_id"],  # Our generated ID (primary)
            ["request_data", "litellm_metadata", "model_info", "id"],  # Legacy fallback
        ],
        "async_post_call_streaming_hook": [
            ["data", "metadata", "luthien_call_id"],  # Our generated ID (primary)
            ["data", "litellm_metadata", "model_info", "id"],  # Legacy fallback
        ],
        "async_post_call_failure_hook": [
            ["data", "metadata", "luthien_call_id"],  # Our generated ID (primary)
            ["data", "metadata", "hidden_params", "litellm_call_id"],  # Legacy non-streaming
            ["request_data", "litellm_metadata", "model_info", "id"],  # Legacy streaming
        ],
    }
    paths = hook_to_id_paths.get(hook)
    if paths is None:
        logging.warning(f"No call_id path defined for hook '{hook}'")
        return None

    if not paths:
        # Empty list means call_id not available for this hook
        return None

    for path in paths:
        call_id_value = _get_in(payload, path)
        if call_id_value is None:
            continue
        if not isinstance(call_id_value, str):
            logging.error(
                f"call_id at path {path} is not a string: {call_id_value}",
            )
            return None
        return call_id_value

    logging.warning(f"Could not find call_id at any known path for hook '{hook}'")
    return None
