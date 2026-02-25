# ABOUTME: Pytest configuration for integration tests
# ABOUTME: Loads environment variables and provides database fixtures

"""Integration test configuration.

Loads .env file and provides shared fixtures for tests that require
real infrastructure (PostgreSQL, Redis, etc.).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root, converting docker hostnames to localhost
_repo_root = Path(__file__).resolve().parents[2]
load_dotenv(_repo_root / ".env")

# Docker compose uses 'db' as the hostname, but from the host machine
# we need to use 'localhost' since the port is forwarded
_db_url = os.environ.get("DATABASE_URL", "")
if "@db:" in _db_url:
    os.environ["DATABASE_URL"] = _db_url.replace("@db:", "@localhost:")
