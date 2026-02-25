#!/usr/bin/env bash
# Requires: bash 3.2+
set -euo pipefail

uv run ruff format
uv run ruff check --fix
echo "Formatting complete."
