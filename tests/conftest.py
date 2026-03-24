import os
from pathlib import Path

# Duplicated from tests.constants — conftest is loaded before pytest's
# import hooks activate, so it can't import from tests.constants.
REPO_ROOT = Path(__file__).resolve().parents[1]


def pytest_sessionstart(session):
    os.environ.setdefault("POLICY_CONFIG", str(REPO_ROOT / "config" / "policy_config.yaml"))
    os.environ.setdefault("OTEL_ENABLED", "false")


def pytest_configure(config):
    """Disable timeout for e2e test runs."""
    markexpr = config.getoption("-m", default="")
    if "e2e" in markexpr and "not e2e" not in markexpr:
        config.option.timeout = 0
