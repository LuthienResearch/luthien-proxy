"""Pytest configuration for saas_infra tests."""

import sys
from pathlib import Path

# Add the repo root to Python path so saas_infra is importable
repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
