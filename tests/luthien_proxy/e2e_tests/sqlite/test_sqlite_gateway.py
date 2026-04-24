"""Re-run mock e2e tests against a SQLite-backed gateway.

Each test function is imported from the original mock e2e test modules.
The conftest in this directory overrides gateway_url/api_key/admin_api_key
fixtures so the imported tests hit the in-process SQLite gateway.

Run:  uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/sqlite/ -v --timeout=30
"""

import pytest

# --- Admin API ---
from tests.luthien_proxy.e2e_tests.test_mock_admin_api import (
    test_policy_list_includes_known_policies,
)

# --- Basic passthrough ---
from tests.luthien_proxy.e2e_tests.test_mock_basic import (
    test_default_response_when_queue_empty,
    test_non_streaming_passthrough,
    test_streaming_passthrough,
)

# --- Error handling ---
from tests.luthien_proxy.e2e_tests.test_mock_error_handling import (
    test_backend_400_propagates_error_response,
    test_backend_429_propagates_error_response,
    test_backend_500_propagates_error_response,
    test_missing_auth_header_returns_401,
    test_missing_messages_field_returns_400,
)

# --- Inference provider registry ---
from tests.luthien_proxy.e2e_tests.test_mock_inference_providers import (
    test_inference_provider_crud_roundtrip,
    test_inference_provider_delete_missing_returns_404,
    test_inference_provider_unknown_backend_returns_400,
)

# --- Onboarding policy ---
from tests.luthien_proxy.e2e_tests.test_mock_onboarding_policy import (
    test_first_turn_appends_welcome,
    test_first_turn_streaming_appends_welcome,
    test_first_turn_streaming_tool_use_no_crash,
    test_first_turn_tool_use_no_crash,
    test_second_turn_passthrough,
)

# --- Policies ---
from tests.luthien_proxy.e2e_tests.test_mock_policies import (
    test_all_caps_non_streaming,
    test_all_caps_streaming,
    test_policy_non_streaming_smoke,
    test_policy_streaming_smoke,
    test_string_replacement_non_streaming,
    test_string_replacement_streaming,
)

# --- Policy management ---
from tests.luthien_proxy.e2e_tests.test_mock_policy_management import (
    test_get_current_policy_returns_policy_info,
    test_policy_takes_effect_on_next_request,
    test_set_invalid_policy_returns_error,
    test_set_policy_changes_active_policy,
)

# --- Request forwarding ---
from tests.luthien_proxy.e2e_tests.test_mock_request_forwarding import (
    test_metadata_forwarded,
    test_model_forwarded,
    test_system_prompt_forwarded,
    test_temperature_forwarded,
)

# --- Session history ---
from tests.luthien_proxy.e2e_tests.test_mock_session_history import (
    test_session_list_includes_recent_session,
    test_session_stored_after_request,
)

# --- Special characters ---
from tests.luthien_proxy.e2e_tests.test_mock_special_chars import (
    test_allcaps_passes_through_emoji,
    test_noop_policy_preserves_unicode,
)

# --- Streaming structure ---
from tests.luthien_proxy.e2e_tests.test_mock_streaming_structure import (
    test_anthropic_streaming_event_lifecycle,
    test_anthropic_streaming_message_start_structure,
)

# Marker so these don't run with default `uv run pytest`
pytestmark = pytest.mark.sqlite_e2e

# Re-export so pytest collects them
__all__ = [
    # basic
    "test_non_streaming_passthrough",
    "test_streaming_passthrough",
    "test_default_response_when_queue_empty",
    # errors
    "test_backend_400_propagates_error_response",
    "test_backend_429_propagates_error_response",
    "test_backend_500_propagates_error_response",
    "test_missing_auth_header_returns_401",
    "test_missing_messages_field_returns_400",
    # admin
    "test_policy_list_includes_known_policies",
    # inference provider registry
    "test_inference_provider_crud_roundtrip",
    "test_inference_provider_unknown_backend_returns_400",
    "test_inference_provider_delete_missing_returns_404",
    # policy management
    "test_get_current_policy_returns_policy_info",
    "test_set_policy_changes_active_policy",
    "test_set_invalid_policy_returns_error",
    "test_policy_takes_effect_on_next_request",
    # policies
    "test_policy_non_streaming_smoke",
    "test_policy_streaming_smoke",
    "test_all_caps_non_streaming",
    "test_all_caps_streaming",
    "test_string_replacement_non_streaming",
    "test_string_replacement_streaming",
    # forwarding
    "test_model_forwarded",
    "test_metadata_forwarded",
    "test_system_prompt_forwarded",
    "test_temperature_forwarded",
    # sessions
    "test_session_stored_after_request",
    "test_session_list_includes_recent_session",
    # streaming
    "test_anthropic_streaming_event_lifecycle",
    "test_anthropic_streaming_message_start_structure",
    # special chars
    "test_allcaps_passes_through_emoji",
    "test_noop_policy_preserves_unicode",
    # onboarding policy
    "test_first_turn_appends_welcome",
    "test_second_turn_passthrough",
    "test_first_turn_streaming_appends_welcome",
    "test_first_turn_tool_use_no_crash",
    "test_first_turn_streaming_tool_use_no_crash",
]
