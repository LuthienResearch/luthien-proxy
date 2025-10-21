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
    os.environ.setdefault("LUTHIEN_POLICY_CONFIG", str(REPO_ROOT / "config" / "luthien_config.yaml"))

    # Disable OpenTelemetry export during tests to avoid noisy errors
    # (OTel tries to export to tempo:4317 which doesn't exist in test environment)
    os.environ.setdefault("OTEL_ENABLED", "false")
