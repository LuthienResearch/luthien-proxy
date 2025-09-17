import os
import sys
from pathlib import Path

# Ensure the src/ directory is importable in tests without extra tooling.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def pytest_sessionstart(session):
    # Avoid accidental reliance on production defaults during unit tests.
    os.environ.setdefault(
        "LUTHIEN_POLICY_CONFIG", str(REPO_ROOT / "config" / "luthien_config.yaml")
    )
