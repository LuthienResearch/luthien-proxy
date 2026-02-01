Objective: Fix Codex chat wire API rendering issue (token-per-line / bullet layout) when running through Luthien.

Acceptance:
- Reproducible failing test captures the bad SSE/formatting behavior (TDD).
- Fix ensures Codex renders normal lines (no token-per-line/bullet output).
- Test passes and remains fast.

Plan (TDD):
1. Add a unit test that validates SSE output formatting for Codex chat wire API path.
2. Reproduce failure locally (test fails).
3. Implement fix.
4. Update tests + run targeted suite.
