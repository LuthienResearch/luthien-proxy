---
category: Fixes
---

**E2E suite: fix UAT mock tests and unblock real tier**:
  - Fix `test_mock_uat01_onboarding_context.py`: format `_WELCOME_SETUP_HINT` with `gateway_url` before substring check (the unformatted template literal `{gateway_url}` could never appear in rendered output).
  - Fix `test_mock_uat03_unimplemented_policies.py`: assert `200 {success: false, error}` matches the actual `/api/admin/policy/set` contract for unloadable policies, not 400/422; correct `_ADMIN_POLICY_GET_PATH` to `/api/admin/policy/current`.
  - Fix `test_mock_uat04_api_key_errors.py::test_wrong_admin_key_returns_clear_error`: toggle `LOCALHOST_AUTH_BYPASS` off via the config API for the duration of the test so wrong-key auth is actually exercised.
  - Fix `test_mock_uat05_policy_stability.py`: same admin-policy GET path fix as UAT03.
  - Fix Docker boot on real tier: set `UV_NO_SYNC=1` on the gateway service so `uv run` doesn't re-sync at runtime — that re-sync invokes hatch-vcs, which writes `_version.py` into the read-only `./src` mount.
  - Don't gate the entire real tier on `ANTHROPIC_API_KEY`: judge-policy tests already self-skip when missing, and the rest of the tier works via OAuth/passthrough.
