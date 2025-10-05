"""ABOUTME: Parameterized E2E tests for all Luthien policies.
ABOUTME: Tests each policy in both streaming and non-streaming modes using shared test infrastructure.
"""

from __future__ import annotations

import pytest
from tests.e2e_tests.helpers import (
    ALL_POLICY_TEST_CASES,
    E2ESettings,
    PolicyTestCase,
    assert_debug_log,
    assert_response_expectations,
    execute_non_streaming_request,
    execute_streaming_request,
    extract_message_content,
    extract_streaming_content,
)

pytestmark = pytest.mark.e2e


# ==============================================================================
# Test Parametrization & Fixtures
# ==============================================================================


def pytest_generate_tests(metafunc):
    """Generate test parametrization with proper fixture setup."""
    if "test_case" in metafunc.fixturenames:
        # Parametrize test cases
        metafunc.parametrize("test_case", ALL_POLICY_TEST_CASES, ids=lambda tc: tc.test_id, indirect=False)


@pytest.fixture(scope="function")
def policy_config_path(test_case: PolicyTestCase) -> str:
    """Extract policy config path from the test case."""
    return test_case.policy_config_path


@pytest.fixture(scope="function")
def use_test_policy(
    control_plane_manager,
    policy_config_path: str,
):
    """Apply the policy for this specific test case."""
    with control_plane_manager.apply_policy(policy_config_path):
        yield


# ==============================================================================
# Parameterized Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_policy_non_streaming(
    test_case: PolicyTestCase,
    use_test_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Test policy behavior in non-streaming mode."""
    for turn_index, turn in enumerate(test_case.turns):
        response_body, call_id = await execute_non_streaming_request(e2e_settings, turn.request)
        content = extract_message_content(response_body)

        if e2e_settings.verbose:
            print(f"[Turn {turn_index}] Response content: {content}")

        assert_response_expectations(response_body, turn.expected_response, content)
        await assert_debug_log(e2e_settings, call_id, turn.expected_response)


@pytest.mark.asyncio
async def test_policy_streaming(
    test_case: PolicyTestCase,
    use_test_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Test policy behavior in streaming mode."""
    for turn_index, turn in enumerate(test_case.turns):
        chunks, call_id = await execute_streaming_request(e2e_settings, turn.request)
        content = extract_streaming_content(chunks)

        if e2e_settings.verbose:
            print(f"[Turn {turn_index}] Streaming content: {content}")

        assert_response_expectations(chunks, turn.expected_response, content)
        await assert_debug_log(e2e_settings, call_id, turn.expected_response)
