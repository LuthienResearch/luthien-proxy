from __future__ import annotations

from typing import Any, Optional


def _get_in(d: dict[str, Any], path: list[str]) -> Optional[Any]:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def extract_call_id_for_hook(hook: str, payload: dict[str, Any]) -> Optional[str]:
    """Deterministic extraction of litellm_call_id per hook.

    Keep this in one place to avoid drift across endpoints.
    """
    name = (hook or "").lower()
    paths: list[list[str]] = []
    # Generic fallbacks used by several hooks
    common_kwarg_paths = [
        ["kwargs", "litellm_call_id"],
        ["kwargs", "litellm_params", "litellm_call_id"],
        ["kwargs", "litellm_params", "metadata", "hidden_params", "litellm_call_id"],
    ]
    if name in {"async_logging_hook", "logging_hook"}:
        paths = [
            ["kwargs", "kwargs", "litellm_call_id"],
            ["kwargs", "kwargs", "litellm_params", "litellm_call_id"],
            [
                "kwargs",
                "kwargs",
                "litellm_params",
                "metadata",
                "hidden_params",
                "litellm_call_id",
            ],
        ]
    elif name in {
        "log_pre_api_call",
        "log_post_api_call",
        "async_log_success_event",
        "log_success_event",
    }:
        paths = common_kwarg_paths
    elif name in {
        "async_post_call_success_hook",
        "async_pre_call_hook",
        "async_moderation_hook",
    }:
        paths = [
            ["kwargs", "data", "litellm_call_id"],
            ["kwargs", "data", "metadata", "hidden_params", "litellm_call_id"],
        ]
    elif name in {"async_pre_call_deployment_hook"}:
        paths = [["kwargs", "kwargs", "litellm_call_id"]]
    elif name in {
        "async_post_call_success_deployment_hook",
        "async_post_call_streaming_iterator_hook",
    }:
        paths = [["kwargs", "request_data", "litellm_call_id"]]
    elif name in {"kwargs_pre", "kwargs_post"}:
        paths = common_kwarg_paths + [
            ["kwargs", "metadata", "hidden_params", "litellm_call_id"]
        ]
    else:
        # Try generic common paths as a last resort
        paths = common_kwarg_paths + [
            ["kwargs", "kwargs", "litellm_call_id"],
            ["kwargs", "request_data", "litellm_call_id"],
        ]

    for p in paths:
        v = _get_in(payload, p)
        if isinstance(v, str) and v:
            return v
    return None


def extract_call_id_from_request_data(
    request_data: dict[str, Any] | None,
) -> Optional[str]:
    rd = request_data or {}
    for path in (
        ["litellm_call_id"],
        ["metadata", "hidden_params", "litellm_call_id"],
        ["kwargs", "litellm_call_id"],
        ["kwargs", "litellm_params", "litellm_call_id"],
    ):
        v = _get_in(rd, path)
        if isinstance(v, str) and v:
            return v
    return None
