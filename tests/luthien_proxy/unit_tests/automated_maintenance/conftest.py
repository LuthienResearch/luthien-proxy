"""Shared fixtures for automated_maintenance unit tests.

Centralises the path resolution that test modules use to import the
script-tree Python files (`dashboard.py`, `path_gate.py`). Computing
`Path(__file__).parents[4]` in each test file is brittle to moves;
keep it here so a tree reorganisation only requires editing one place.
"""

from __future__ import annotations

from pathlib import Path

# repo_root / scripts / automated_maintenance / lib
AUTOMATED_MAINTENANCE_LIB = Path(__file__).resolve().parents[4] / "scripts" / "automated_maintenance" / "lib"
