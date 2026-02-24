"""Shared test constants.

Centralizes model IDs and other magic strings so they can be updated in one place
when Anthropic releases new model versions.
"""

# Cheapest Claude model for tests that make real API calls (e2e tests, credential validation).
DEFAULT_CLAUDE_TEST_MODEL = "claude-haiku-4-5"

# Model that supports extended thinking (needed for thinking-specific e2e tests).
CLAUDE_THINKING_MODEL = "claude-sonnet-4-5"
