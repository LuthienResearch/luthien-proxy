"""Shared utilities for real-API e2e tests.

Extracted here so unit tests can import them without triggering the
module-level ANTHROPIC_API_KEY guard in test_real_api_threat_scenarios.py.
"""

import asyncio
import functools
import logging

_logger = logging.getLogger(__name__)


def retry_on_assertion(max_retries: int = 3, base_delay: float = 2.0):
    """Retry async tests on AssertionError with linear backoff (delay = base_delay * attempt).

    Handles LLM non-determinism — the real judge may occasionally make
    an unexpected decision.  Retrying gives it another chance before we
    capture the failure for analysis.

    Note: accesses failure_capture from kwargs only — pytest injects fixtures
    as keyword arguments, so this works correctly in practice. If failure_capture
    is passed positionally, reset() silently skips between retries.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc: AssertionError | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except AssertionError as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        capture = kwargs.get("failure_capture")
                        if capture is not None:
                            capture.reset()
                        delay = base_delay * attempt
                        _logger.warning(
                            "Attempt %d/%d failed (%s) — retrying in %.0fs",
                            attempt,
                            max_retries,
                            func.__name__,
                            delay,
                        )
                        await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


def extract_text(data: dict) -> str:
    """Extract concatenated text from Anthropic response content blocks."""
    return " ".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
