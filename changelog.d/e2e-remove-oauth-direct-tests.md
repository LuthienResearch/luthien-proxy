---
category: Chores & Docs
pr: 747
---

**Remove e2e tests that misuse Bearer auth with API keys**: Deleted three passthrough-auth tests and one streaming-chunk test that sent `Authorization: Bearer <sk-ant-...>`. Anthropic only accepts API keys via `x-api-key`; Bearer is reserved for OAuth, whose automated use Anthropic forbids. Replaced the broken probe fixture with `gateway_passthrough_mode`, which reads `auth_mode` from the admin API. The buffered tool-call streaming behavior remains covered by `mock_e2e` tests.
