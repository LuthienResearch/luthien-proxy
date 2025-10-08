from luthien_proxy.control_plane.utils.hooks import extract_call_id_for_hook


def test_extract_call_id_known_hooks():
    assert extract_call_id_for_hook("async_pre_call_hook", {"data": {"litellm_call_id": "A"}}) == "A"
    assert (
        extract_call_id_for_hook(
            "async_post_call_success_hook",
            {"data": {"litellm_call_id": "B"}},
        )
        == "B"
    )
    assert (
        extract_call_id_for_hook(
            "async_post_call_streaming_iterator_hook",
            {"request_data": {"litellm_call_id": "C"}},
        )
        == "C"
    )
    assert (
        extract_call_id_for_hook(
            "async_post_call_streaming_hook",
            {"data": {"litellm_call_id": "D"}},
        )
        == "D"
    )


def test_extract_call_id_missing_value_returns_none():
    assert extract_call_id_for_hook("async_pre_call_hook", {"data": {"not_the_key": "value"}}) is None


def test_extract_call_id_unknown_hook_returns_none():
    assert extract_call_id_for_hook("async_log_failure_event", {}) is None
