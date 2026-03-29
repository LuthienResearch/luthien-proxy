"""Unit tests for retry_on_assertion decorator and _extract_text helper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.luthien_proxy.e2e_tests.real_api_utils import extract_text, retry_on_assertion

# =============================================================================
# _extract_text
# =============================================================================


def test_extract_text_single_block():
    data = {"content": [{"type": "text", "text": "hello"}]}
    assert extract_text(data) == "hello"


def test_extract_text_multiple_blocks():
    data = {"content": [{"type": "text", "text": "foo"}, {"type": "text", "text": "bar"}]}
    assert extract_text(data) == "foo bar"


def test_extract_text_skips_non_text_blocks():
    data = {
        "content": [
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
            {"type": "text", "text": "result"},
        ]
    }
    assert extract_text(data) == "result"


def test_extract_text_empty_content():
    assert extract_text({"content": []}) == ""


def test_extract_text_missing_content_key():
    assert extract_text({}) == ""


def test_extract_text_missing_text_key_in_block():
    data = {"content": [{"type": "text"}]}
    assert extract_text(data) == ""


# =============================================================================
# retry_on_assertion
# =============================================================================


@pytest.mark.asyncio
async def test_retry_succeeds_on_first_attempt():
    call_count = 0

    @retry_on_assertion(max_retries=3, base_delay=0)
    async def fn():
        nonlocal call_count
        call_count += 1

    await fn()
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_retries_on_assertion_error():
    call_count = 0

    @retry_on_assertion(max_retries=3, base_delay=0)
    async def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise AssertionError("not yet")

    await fn()
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_raises_after_max_retries():
    @retry_on_assertion(max_retries=2, base_delay=0)
    async def fn():
        raise AssertionError("always fails")

    with pytest.raises(AssertionError, match="always fails"):
        await fn()


@pytest.mark.asyncio
async def test_retry_resets_failure_capture_between_attempts():
    reset_calls = 0
    capture = MagicMock()

    def count_reset():
        nonlocal reset_calls
        reset_calls += 1

    capture.reset = count_reset
    call_count = 0

    @retry_on_assertion(max_retries=3, base_delay=0)
    async def fn(failure_capture=None):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise AssertionError("fail")

    await fn(failure_capture=capture)
    assert reset_calls == 2  # called between attempt 1→2 and 2→3, not after final


@pytest.mark.asyncio
async def test_retry_does_not_reset_when_no_capture():
    @retry_on_assertion(max_retries=2, base_delay=0)
    async def fn():
        raise AssertionError("fail")

    with pytest.raises(AssertionError):
        await fn()


@pytest.mark.asyncio
async def test_retry_uses_linear_backoff():
    sleep_calls = []

    @retry_on_assertion(max_retries=3, base_delay=1.0)
    async def fn():
        raise AssertionError("fail")

    with patch(
        "tests.luthien_proxy.e2e_tests.real_api_utils.asyncio.sleep",
        new=AsyncMock(side_effect=lambda d: sleep_calls.append(d)),
    ):
        with pytest.raises(AssertionError):
            await fn()

    assert sleep_calls == [1.0, 2.0]  # linear: 1*1, 2*1; no sleep after final attempt
