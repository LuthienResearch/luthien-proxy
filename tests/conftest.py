import os
import sys
from pathlib import Path

# Ensure the src/ directory is importable in tests without extra tooling.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Also add repo root for config/ imports (used by litellm_callback tests)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_sessionstart(session):
    # Avoid accidental reliance on production defaults during unit tests.
    os.environ.setdefault("V2_POLICY_CONFIG", str(REPO_ROOT / "config" / "v2_config.yaml"))

    # Disable OpenTelemetry export during tests to avoid noisy errors
    # (OTel tries to export to tempo:4317 which doesn't exist in test environment)
    os.environ.setdefault("OTEL_ENABLED", "false")


def pytest_configure(config):
    """Configure pytest timeout behavior based on test markers.

    E2E tests are exempt from the default 3-second timeout since they may
    involve real infrastructure and take longer to complete.
    """
    # Check if we're running e2e tests
    markexpr = config.getoption("-m", default="")
    if "e2e" in markexpr and "not e2e" not in markexpr:
        # Disable timeout for e2e tests
        config.option.timeout = 0
