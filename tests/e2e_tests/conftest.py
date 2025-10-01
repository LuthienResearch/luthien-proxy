from __future__ import annotations

import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e_tests.helpers import (  # noqa: E402
    ControlPlaneManager,
    E2ESettings,
    ensure_services_available,
    load_e2e_settings,
)


@pytest.fixture(scope="session")
def e2e_settings() -> E2ESettings:
    return load_e2e_settings()


@pytest.fixture(scope="session")
def control_plane_manager(e2e_settings: E2ESettings) -> ControlPlaneManager:
    return ControlPlaneManager(e2e_settings)


@pytest.fixture(scope="module")
def use_sql_policy(control_plane_manager: ControlPlaneManager, e2e_settings: E2ESettings):
    with control_plane_manager.apply_policy(e2e_settings.target_policy_config):
        yield


@pytest.fixture(scope="module")
async def ensure_stack_ready(e2e_settings: E2ESettings):
    await ensure_services_available(e2e_settings)
