---
category: Fixes
---

**README troubleshooting no longer assumes a proxy key that onboarding never sets**: The
"API requests failing" steps used to tell users to check their `Authorization: Bearer
<PROXY_API_KEY>` header first, but `luthien onboard` runs in `AUTH_MODE=both` and never
writes a `PROXY_API_KEY` — clients authenticate via their passthrough upstream credential
(OAuth session or `ANTHROPIC_API_KEY`). The troubleshooting list now leads with upstream
credentials and treats `PROXY_API_KEY` as an opt-in that can be skipped if you didn't set
one.
