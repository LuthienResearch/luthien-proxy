"""Helpers to extract a stable litellm_call_id from varied hook payloads."""

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
    """Return the `litellm_call_id` for a given hook payload.

    Why: LiteLLM emits slightly different shapes across hooks. Keeping a
    single, table‑driven extractor avoids drift and long if/elif chains.
    """
    name = (hook or "").lower()

    # Common paths seen across multiple hooks
    common_kwarg_paths: list[list[str]] = [
        ["kwargs", "litellm_call_id"],
        ["kwargs", "litellm_params", "litellm_call_id"],
        ["kwargs", "litellm_params", "metadata", "hidden_params", "litellm_call_id"],
    ]

    # Exact hook → path lists. Keep this compact and explicit.
    mapping: dict[str, list[list[str]]] = {
        # Deployment and pre hooks
        "async_pre_call_deployment_hook": [["kwargs", "kwargs", "litellm_call_id"]],
        "async_pre_call_hook": [
            ["data", "litellm_call_id"],
            ["kwargs", "data", "litellm_call_id"],
        ],
        "async_moderation_hook": [["kwargs", "data", "litellm_call_id"]],
        # Post hooks
        "async_post_call_success_hook": [
            ["request_data", "litellm_call_id"],
            ["kwargs", "request_data", "litellm_call_id"],
        ],
        "async_post_call_streaming_iterator_hook": [
            ["request_data", "litellm_call_id"],
            ["kwargs", "request_data", "litellm_call_id"],
        ],
        "async_post_call_success_deployment_hook": [["kwargs", "request_data", "litellm_call_id"]],
        # Logging style hooks
        "async_logging_hook": [
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
        ],
        "logging_hook": [
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
        ],
        # Event logs
        "log_pre_api_call": common_kwarg_paths,
        "log_post_api_call": common_kwarg_paths,
        "async_log_success_event": common_kwarg_paths,
        "log_success_event": common_kwarg_paths,
        # Internal helpers
        "kwargs_pre": common_kwarg_paths + [["kwargs", "metadata", "hidden_params", "litellm_call_id"]],
        "kwargs_post": common_kwarg_paths + [["kwargs", "metadata", "hidden_params", "litellm_call_id"]],
    }

    candidate_paths = (
        mapping.get(name, [])
        + common_kwarg_paths
        + [
            ["kwargs", "kwargs", "litellm_call_id"],
            ["kwargs", "request_data", "litellm_call_id"],
        ]
    )

    for path in candidate_paths:
        v = _get_in(payload, path)
        if isinstance(v, str) and v:
            return v
    return None


def extract_call_id_from_request_data(
    request_data: dict[str, Any] | None,
) -> Optional[str]:
    """Best-effort extraction of litellm_call_id from request_data fields."""
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
