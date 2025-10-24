"""Helper utilities for end-to-end tests."""

from .callback_assertions import (
    clear_callback_trace,
    get_callback_invocations,
)
from .docker_logs import (
    extract_stream_ids,
    filter_logs_by_pattern,
    find_most_recent_match,
    get_container_logs,
    get_control_plane_logs,
    get_litellm_logs,
)
from .infra import (
    ControlPlaneManager,
    E2ESettings,
    dummy_provider_running,
    ensure_services_available,
    load_e2e_settings,
)
from .policy_assertions import (
    assert_response_expectations,
    execute_non_streaming_request,
    execute_streaming_request,
    extract_message_content,
    extract_streaming_content,
)
from .policy_test_cases import ALL_POLICY_TEST_CASES
from .policy_test_models import (
    ConversationTurn,
    Message,
    PolicyTestCase,
    RequestSpec,
    ResponseAssertion,
)
from .requests import (
    consume_streaming_response,
    make_nonstreaming_request,
    make_streaming_request,
)
from .v2_gateway import (
    V2GatewayManager,
    wait_for_v2_gateway,
)

__all__ = [
    # Infrastructure
    "ControlPlaneManager",
    "E2ESettings",
    "dummy_provider_running",
    "ensure_services_available",
    "load_e2e_settings",
    # V2 Gateway
    "V2GatewayManager",
    "wait_for_v2_gateway",
    # Docker logs
    "extract_stream_ids",
    "filter_logs_by_pattern",
    "find_most_recent_match",
    "get_container_logs",
    "get_control_plane_logs",
    "get_litellm_logs",
    # Requests
    "consume_streaming_response",
    "make_nonstreaming_request",
    "make_streaming_request",
    # Policy test models
    "Message",
    "RequestSpec",
    "ResponseAssertion",
    "ConversationTurn",
    "PolicyTestCase",
    # Policy test cases
    "ALL_POLICY_TEST_CASES",
    # Policy assertions
    "execute_non_streaming_request",
    "execute_streaming_request",
    "extract_message_content",
    "extract_streaming_content",
    "assert_response_expectations",
    # Callback assertions
    "get_callback_invocations",
    "clear_callback_trace",
]
