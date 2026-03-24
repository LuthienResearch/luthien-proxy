import os

from tests.constants import REPO_ROOT


def pytest_sessionstart(session):
    os.environ.setdefault("POLICY_CONFIG", str(REPO_ROOT / "config" / "policy_config.yaml"))
    os.environ.setdefault("OTEL_ENABLED", "false")


def pytest_configure(config):
    """Disable timeout for e2e test runs."""
    markexpr = config.getoption("-m", default="")
    if "e2e" in markexpr and "not e2e" not in markexpr:
        config.option.timeout = 0
