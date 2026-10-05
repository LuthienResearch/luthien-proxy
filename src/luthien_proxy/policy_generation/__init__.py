"""Utilities that generate Luthien policy YAML from external sources.

Currently supports generating a `SimpleLLMPolicy` configuration from a
project's CLAUDE.md / AGENTS.md file (`claude_md` module). Run it with:

    uv run python -m luthien_proxy.policy_generation.claude_md path/to/CLAUDE.md

Note: this package intentionally avoids importing submodules at package level
so `python -m luthien_proxy.policy_generation.claude_md` runs without a
double-import warning. Import from `luthien_proxy.policy_generation.claude_md`
directly.
"""
