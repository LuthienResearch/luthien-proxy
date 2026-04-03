import os
from pathlib import Path

# Duplicated from tests.constants — conftest is loaded before pytest's
# import hooks activate, so it can't import from tests.constants.
REPO_ROOT = Path(__file__).resolve().parents[1]

# Force-set BEFORE any other imports. litellm's __init__ calls load_dotenv()
# which picks up SENTRY_ENABLED=true from the repo's .env. pytest_sessionstart
# runs too late (after conftest imports), so setdefault can't win the race.
# Force-override ensures Sentry is never initialized during tests.
os.environ["SENTRY_ENABLED"] = "false"
os.environ["SENTRY_DSN"] = ""


def pytest_sessionstart(session):
    os.environ.setdefault("POLICY_CONFIG", str(REPO_ROOT / "config" / "policy_config.yaml"))
    os.environ.setdefault("OTEL_ENABLED", "false")
    os.environ.setdefault("ENVIRONMENT", "test")


def pytest_configure(config):
    """Disable timeout for e2e test runs."""
    markexpr = config.getoption("-m", default="")
    if "e2e" in markexpr and "not e2e" not in markexpr:
        config.option.timeout = 0
