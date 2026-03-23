"""Mock e2e tests for policy handling of non-ASCII text and special characters.

Verifies that policies correctly handle (or gracefully pass through) Unicode,
emoji, accented characters, and strings containing regex metacharacters.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d
  - Mock server auto-started by the mock_anthropic fixture (port 18888).

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_special_chars.py -v
"""

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": False,
}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}

_ALL_CAPS_CLASS_REF = "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
_NOOP_CLASS_REF = "luthien_proxy.policies.noop_policy:NoOpPolicy"
_STRING_REPLACEMENT_CLASS_REF = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"


def _extract_text(response: httpx.Response) -> str:
    """Extract the text from the first content block of an Anthropic response."""
    return response.json()["content"][0]["text"]


@pytest.mark.asyncio
async def test_allcaps_passes_through_emoji(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """AllCapsPolicy does not crash when the response contains emoji characters."""
    mock_anthropic.enqueue(text_response("hello 🌍"))

    async with policy_context(_ALL_CAPS_CLASS_REF, {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json=_BASE_REQUEST,
                headers=_HEADERS,
            )

    assert response.status_code == 200, f"Expected 200 with emoji input, got {response.status_code}: {response.text}"
    # Just verify no 500 and the response is parseable — emoji handling may vary
    data = response.json()
    assert data["type"] == "message"
    assert len(data["content"]) > 0


@pytest.mark.asyncio
async def test_allcaps_with_accented_characters(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """AllCapsPolicy uppercases accented characters correctly (Python .upper() handles Unicode)."""
    mock_anthropic.enqueue(text_response("café résumé"))

    async with policy_context(_ALL_CAPS_CLASS_REF, {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json=_BASE_REQUEST,
                headers=_HEADERS,
            )

    assert response.status_code == 200, f"Expected 200 with accented input, got {response.status_code}: {response.text}"
    text = _extract_text(response)
    assert text == text.upper(), f"Expected all-uppercase text, got: {text!r}"
    # Python's str.upper() produces "CAFÉ RÉSUMÉ"
    assert "CAFÉ" in text or "CAF" in text, f"Uppercased accented word missing from: {text!r}"


@pytest.mark.asyncio
async def test_string_replacement_with_unicode_target(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """StringReplacementPolicy replaces a plain word with a Unicode/emoji replacement string."""
    mock_anthropic.enqueue(text_response("bonjour monde"))

    async with policy_context(
        _STRING_REPLACEMENT_CLASS_REF,
        {"replacements": [["monde", "🌍"]], "match_capitalization": False},
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json=_BASE_REQUEST,
                headers=_HEADERS,
            )

    assert response.status_code == 200, (
        f"Expected 200 with unicode replacement, got {response.status_code}: {response.text}"
    )
    text = _extract_text(response)
    assert "🌍" in text, f"Expected emoji replacement in response text, got: {text!r}"


@pytest.mark.asyncio
async def test_string_replacement_special_regex_chars(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """StringReplacementPolicy treats the search string literally, not as a regex pattern.

    Dollar signs and dots are regex metacharacters — the policy must escape them
    before passing to re.sub() (or use a literal replacement approach).
    """
    mock_anthropic.enqueue(text_response("price: $100.00"))

    async with policy_context(
        _STRING_REPLACEMENT_CLASS_REF,
        {"replacements": [["$100.00", "€90.00"]], "match_capitalization": False},
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json=_BASE_REQUEST,
                headers=_HEADERS,
            )

    assert response.status_code == 200, (
        f"Expected 200 with regex-special replacement target, got {response.status_code}: {response.text}"
    )
    text = _extract_text(response)
    assert "€90.00" in text, f"Expected euro replacement in response text, got: {text!r}"
    assert "$100.00" not in text, f"Original price should have been replaced in: {text!r}"


@pytest.mark.asyncio
async def test_noop_policy_preserves_unicode(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """NoOpPolicy passes multi-byte Unicode text through without any modification."""
    original_text = "日本語テスト"
    mock_anthropic.enqueue(text_response(original_text))

    async with policy_context(_NOOP_CLASS_REF, {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json=_BASE_REQUEST,
                headers=_HEADERS,
            )

    assert response.status_code == 200, f"Expected 200 with Japanese text, got {response.status_code}: {response.text}"
    text = _extract_text(response)
    assert text == original_text, f"NoOpPolicy must preserve text exactly. Expected {original_text!r}, got {text!r}"
