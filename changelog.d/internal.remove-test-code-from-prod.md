Moved the test-only `PolicyContext.for_testing()` factory out of the production
`policy_core` module into a test fixture (`make_policy_context()` in
`tests/luthien_proxy/fixtures/policy_context.py`). No user-facing behavior change.
