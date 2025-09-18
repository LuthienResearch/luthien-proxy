from luthien_proxy.control_plane.utils.hooks import (
    extract_call_id_for_hook,
    extract_call_id_from_request_data,
)


def test_extract_call_id_specific_hooks():
    assert extract_call_id_for_hook("async_pre_call_deployment_hook", {"kwargs": {"litellm_call_id": "A"}}) == "A"
    assert extract_call_id_for_hook("async_pre_call_hook", {"data": {"litellm_call_id": "B"}}) == "B"
    assert extract_call_id_for_hook("async_post_call_success_hook", {"request_data": {"litellm_call_id": "C"}}) == "C"
    assert (
        extract_call_id_for_hook(
            "async_post_call_streaming_iterator_hook",
            {"request_data": {"litellm_call_id": "D"}},
        )
        == "D"
    )


def test_extract_call_id_logging_and_common_paths():
    assert extract_call_id_for_hook("logging_hook", {"kwargs": {"kwargs": {"litellm_call_id": "X"}}}) == "X"
    # Common kwargs paths
    assert (
        extract_call_id_for_hook(
            "log_pre_api_call",
            {"kwargs": {"litellm_params": {"litellm_call_id": "Y"}}},
        )
        == "Y"
    )
    # Hidden params path
    assert (
        extract_call_id_for_hook(
            "log_success_event",
            {"kwargs": {"litellm_params": {"metadata": {"hidden_params": {"litellm_call_id": "Z"}}}}},
        )
        == "Z"
    )


def test_extract_call_id_from_request_data():
    assert extract_call_id_from_request_data({"litellm_call_id": "A"}) == "A"
    assert extract_call_id_from_request_data({"metadata": {"hidden_params": {"litellm_call_id": "B"}}}) == "B"
    assert extract_call_id_from_request_data({"kwargs": {"litellm_params": {"litellm_call_id": "C"}}}) == "C"
    # None and no-match cases
    assert extract_call_id_from_request_data(None) is None
    assert extract_call_id_from_request_data({}) is None
