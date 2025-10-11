from luthien_proxy.control_plane.utils.hooks import extract_call_id_for_hook


def test_extract_call_id_known_hooks():
    # async_pre_call_hook doesn't extract - we generate the ID in hooks_routes.py instead
    assert extract_call_id_for_hook("async_pre_call_hook", {"data": {"luthien_call_id": "A"}}) is None

    # async_post_call_success_hook checks luthien_call_id first (our generated ID)
    assert (
        extract_call_id_for_hook(
            "async_post_call_success_hook",
            {"data": {"metadata": {"luthien_call_id": "B"}}},
        )
        == "B"
    )
    # Falls back to legacy paths if luthien_call_id not present
    assert (
        extract_call_id_for_hook(
            "async_post_call_success_hook",
            {"data": {"litellm_metadata": {"model_info": {"id": "B_legacy"}}}},
        )
        == "B_legacy"
    )

    # Streaming hooks also check luthien_call_id first
    assert (
        extract_call_id_for_hook(
            "async_post_call_streaming_iterator_hook",
            {"request_data": {"metadata": {"luthien_call_id": "C"}}},
        )
        == "C"
    )
    assert (
        extract_call_id_for_hook(
            "async_post_call_streaming_hook",
            {"data": {"metadata": {"luthien_call_id": "D"}}},
        )
        == "D"
    )


def test_extract_call_id_missing_value_returns_none():
    assert extract_call_id_for_hook("async_pre_call_hook", {"data": {"not_the_key": "value"}}) is None


def test_extract_call_id_unknown_hook_returns_none():
    assert extract_call_id_for_hook("async_log_failure_event", {}) is None
