from __future__ import annotations

import pathlib
import sys
from typing import Optional

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e_tests.helpers import (  # noqa: E402
    ControlPlaneManager,
    E2ESettings,
    V2GatewayManager,
    dummy_provider_running,
    ensure_services_available,
    load_e2e_settings,
)


@pytest.fixture(scope="session")
def e2e_settings() -> E2ESettings:
    return load_e2e_settings()


@pytest.fixture(scope="session")
def control_plane_manager(e2e_settings: E2ESettings) -> ControlPlaneManager:
    return ControlPlaneManager(e2e_settings)


@pytest.fixture(scope="session", autouse=True)
def _ensure_dummy_provider(e2e_settings: E2ESettings):
    with dummy_provider_running(e2e_settings):
        yield


@pytest.fixture(scope="module")
def policy_config_path(e2e_settings: E2ESettings) -> Optional[str]:
    return e2e_settings.target_policy_config


@pytest.fixture(scope="module")
def use_policy(
    control_plane_manager: ControlPlaneManager,
    policy_config_path: Optional[str],
):
    with control_plane_manager.apply_policy(policy_config_path):
        yield


@pytest.fixture(scope="module")
def use_sql_policy(use_policy):  # backward compatibility for existing tests
    yield


@pytest.fixture(scope="module")
async def ensure_stack_ready(e2e_settings: E2ESettings):
    await ensure_services_available(e2e_settings)


@pytest.fixture(scope="module")
def v2_gateway(e2e_settings: E2ESettings):
    """Provide a self-contained V2 gateway instance for testing.

    Starts a V2 gateway on port 8888 with a test-specific API key.
    Does not interfere with dev environment (which uses port 8000).
    """
    manager = V2GatewayManager(
        port=8888,
        api_key="sk-test-v2-gateway",
        verbose=e2e_settings.verbose,
    )
    with manager.running():
        yield manager
