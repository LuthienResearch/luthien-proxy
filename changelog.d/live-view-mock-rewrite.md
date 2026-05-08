---
category: Chores & Docs
pr: 722
---

**Rewrite live-view e2e tests against native Anthropic shape**: `tests/luthien_proxy/e2e_tests/test_conversation_live_view.py` was wholly written against the old OpenAI/LiteLLM response shape and failed at the first response-shape assertion against the current native-Anthropic gateway. Rewritten as `test_mock_conversation_live_view.py` on the `mock_e2e` tier (no real API calls) using the same template as PR #717's history-tests rewrite.
  - Add `_wait_for_session(...)` polling helper to `tests/luthien_proxy/e2e_tests/conftest.py` (replaces fixed `asyncio.sleep` waits in mock_e2e tests; usable by both files going forward).
  - Delete the old e2e file (not skipped — same as #717's treatment).
