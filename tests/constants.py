"""Shared test constants.

Canonical values used across unit, integration, and e2e tests.
Change a constant here to update every test at once.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Unit and integration tests mock the upstream API, so the model value
# doesn't matter there. E2E tests hit the real API — haiku keeps costs low.
DEFAULT_TEST_MODEL = "claude-haiku-4-5-20251001"
