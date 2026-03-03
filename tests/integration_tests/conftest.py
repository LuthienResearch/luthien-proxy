# ABOUTME: Pytest configuration for integration tests
# ABOUTME: Loads environment variables and provides database fixtures

"""Integration test configuration.

Loads .env file and provides shared fixtures for tests that require
real infrastructure (PostgreSQL, Redis, etc.).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from dotenv import load_dotenv

# Re-export DEFAULT_TEST_MODEL from the root tests/conftest.py so that
# `from conftest import DEFAULT_TEST_MODEL` resolves correctly even when
# this local conftest shadows the root one.
_root_conftest_path = Path(__file__).resolve().parent.parent / "conftest.py"
_root_conftest_spec = importlib.util.spec_from_file_location("_root_conftest", _root_conftest_path)
_root_conftest = importlib.util.module_from_spec(_root_conftest_spec)  # type: ignore[arg-type]
_root_conftest_spec.loader.exec_module(_root_conftest)  # type: ignore[union-attr]
DEFAULT_TEST_MODEL: str = _root_conftest.DEFAULT_TEST_MODEL

# Load .env from repo root, converting docker hostnames to localhost
_repo_root = Path(__file__).resolve().parents[2]
load_dotenv(_repo_root / ".env")

# Docker compose uses 'db' as the hostname, but from the host machine
# we need to use 'localhost' since the port is forwarded
_db_url = os.environ.get("DATABASE_URL", "")
if "@db:" in _db_url:
    os.environ["DATABASE_URL"] = _db_url.replace("@db:", "@localhost:")
