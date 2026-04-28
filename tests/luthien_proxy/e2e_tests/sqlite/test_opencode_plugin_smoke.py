"""End-to-end smoke test: real OpenCode + opencode-luthien plugin + gateway + mock providers.

This test is MANUAL ONLY — it requires a real OpenCode binary and the opencode-luthien
plugin installed locally. It is skipped in CI.

To run manually, follow the procedure in dev-README.md under "Track A Smoke Test".
"""

import pytest

pytestmark = pytest.mark.sqlite_e2e


@pytest.mark.skip(reason="manual smoke — requires real OpenCode binary and opencode-luthien plugin installed")
def test_opencode_plugin_happy_path():
    """Happy path: all 3 providers (anthropic, openai, gemini) route through Luthien.

    Procedure:
    1. Run scripts/track_a_smoke.sh
    2. Verify request_logs has 3 rows with session_id, agent, model, endpoint populated
    3. Verify each mock server received x-luthien-session-id and x-luthien-agent headers
    4. Verify plugin's "proxy unreachable" warning does NOT appear in stderr

    Evidence: .sisyphus/evidence/track-a-17-smoke/happy-path/
    """
    pass


@pytest.mark.skip(reason="manual smoke — requires real OpenCode binary and opencode-luthien plugin installed")
def test_opencode_plugin_a6_fallback():
    """A6 fallback: gateway down → plugin warns → OpenCode routes direct → no x-luthien-* headers.

    Procedure:
    1. Run scripts/track_a_smoke.sh --a6-fallback
    2. Verify plugin's "proxy unreachable" warning appears in stderr
    3. Verify OpenCode session exits 0 (chat completed via direct-to-mock fallback)
    4. Verify mock_openai's captured headers do NOT contain x-luthien-* headers

    Evidence: .sisyphus/evidence/track-a-17-smoke/a6-fallback/
    """
    pass
