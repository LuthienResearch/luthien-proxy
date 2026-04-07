# Coverage Documentation: ui/routes.py

**Module:** `src/luthien_proxy/ui/routes.py`

## Coverage Gaps

- UI routes return static HTML files and require FastAPI app context
- SSE streaming endpoints are best tested end-to-end with real HTTP connections
- Auth-protected routes use `check_auth_or_redirect` which is tested in `test_auth.py`

## What's Tested

- `client_setup()` — tested in `test_routes.py::TestClientSetup`
- `landing_page()` — tested in `test_routes.py::TestLandingPage`
- `deprecated_activity_monitor_redirect()` — tested in `test_routes.py::TestDeprecatedRedirects`
- `debug_activity_monitor()` — tested in `test_routes.py::TestDeprecatedRedirects`

## What's Not Tested (by design)

- `diff_viewer()`, `policy_config()`, `credentials_page()`, `request_logs_viewer()`, `conversation_live_view()` — these are simple FileResponse routes behind auth; testing them requires mocking auth or integration tests
- `activity_stream()` — SSE streaming, tested via e2e tests
