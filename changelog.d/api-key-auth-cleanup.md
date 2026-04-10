---
category: Chores & Docs
pr: 524
---

**Clean up PROXY_API_KEY vs ANTHROPIC_API_KEY auth docs**: Rewrite the authentication section in `README.md`, `dev-README.md`, and `CLAUDE.md` so all three auth layers (`PROXY_API_KEY` for client → gateway, `ADMIN_API_KEY` for admin surface, `ANTHROPIC_API_KEY` for gateway → Anthropic) are described consistently and match the default `luthien onboard` flow, which sets only `ADMIN_API_KEY` and relies on OAuth passthrough. Fixes a troubleshooting step that told users to check a `PROXY_API_KEY` header that onboarding never sets, and clarifies that `LOCALHOST_AUTH_BYPASS` covers admin routes too. Restores the empty committed `.env.example` and fixes the generator to emit enum defaults as their `.value` so `AUTH_MODE` renders as `both` instead of `AuthMode.BOTH`. Adds guardrail unit tests that fail loudly if `.env.example` is ever committed empty again or the generator regresses.
