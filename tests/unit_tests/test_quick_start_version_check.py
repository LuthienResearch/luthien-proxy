"""Tests for the Python version check logic in quick_start.sh.

Tests the version comparison logic extracted as a shell function,
verifying it correctly rejects old Python versions and accepts valid ones.
"""

import subprocess

import pytest

# The version check logic extracted from quick_start.sh for testability
VERSION_CHECK_SCRIPT = """
REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=13
PYTHON_VERSION="$1"
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt "$REQUIRED_PYTHON_MAJOR" ] 2>/dev/null || \
   { [ "$PYTHON_MAJOR" -eq "$REQUIRED_PYTHON_MAJOR" ] && [ "$PYTHON_MINOR" -lt "$REQUIRED_PYTHON_MINOR" ]; } 2>/dev/null; then
    echo "REJECT"
    exit 1
fi
echo "ACCEPT"
exit 0
"""


@pytest.mark.parametrize(
    "version,expected",
    [
        ("3.10.0", "REJECT"),
        ("3.12.5", "REJECT"),
        ("3.13.0", "ACCEPT"),
        ("3.13.1", "ACCEPT"),
        ("3.14.0", "ACCEPT"),
        ("2.7.18", "REJECT"),
    ],
)
def test_version_check(version: str, expected: str) -> None:
    """Version check should accept 3.13+ and reject older versions."""
    result = subprocess.run(
        ["bash", "-c", VERSION_CHECK_SCRIPT, "--", version],
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    assert output == expected, f"Python {version}: expected {expected}, got {output} (exit={result.returncode})"
