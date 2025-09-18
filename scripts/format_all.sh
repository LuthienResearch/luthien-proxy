#!/usr/bin/env bash
set -euo pipefail

uv run ruff format
uv run ruff check --fix
echo "Formatting complete."
