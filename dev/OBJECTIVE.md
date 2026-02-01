Objective: Fix Codex chat wire API rendering issue (token-per-line / bullet layout) when running through Luthien.

Acceptance:
- Reproducible failing test captures the bad SSE/formatting behavior (TDD).
- Fix ensures Codex renders normal lines (no token-per-line/bullet output).
- Test passes and remains fast.
- PR description includes full RCA: causal chain, missed signals, and preventative mechanisms.
- PR description includes repro steps + exact warning/error string captured in Codex UI.
- Plan for `/v1/responses` shim documented (what to implement next, why).

Plan (TDD):
1. Add a unit test that validates SSE output formatting for Codex chat wire API path.
2. Reproduce failure locally (test fails).
3. Implement fix.
4. Update tests + run targeted suite.
5. Draft plan for `/v1/responses` shim + minimal test matrix.
