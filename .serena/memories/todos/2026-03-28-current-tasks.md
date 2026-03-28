# Current Tasks — 2026-03-28

## Context
- Demo Day: April 16, 2026 (T-19 days as of Mar 28)
- Active branch: `e2e-owasp-scenarios`
- Both open PRs are blocked on human review

---

## Open PRs

### PR #408 — feat: OWASP threat scenario e2e tests + mock_e2e in CI
- **State**: OPEN, CONFLICTING (merge conflicts on 5 files)
- **Conflicts**: conftest.py, test_mock_auth.py, test_mock_request_logs.py, test_mock_simple_llm_oauth_passthrough.py, test_mock_simple_llm_passthrough_auth.py
- **Jai's request**: Split into smaller PRs:
  - **PR A** (smallest): mock-e2e CI job only (`.github/workflows/dev-checks.yaml`)
  - **PR B**: `scripts/generate_mock_from_failures.py` + clear feature docs
  - **PR C**: Individual fix PRs, each paired with a playback regression test
- **Scott's request**: Write concise Slack message making the case for mock tests (tradeoffs, risk of deprioritizing, why mock approach addresses Jai's cost/speed concerns). First message 1-2 lines + :thread:, details in reply.

### PR #335 — Sentry integration
- **State**: OPEN, awaiting Peter re-review after rebase conflict resolution
- Listed in Trello "This Sprint" under "Merge ready PRs (Paolo's batch)"

---

## Trello Action Items (Paolo-relevant tech work)

| Task | Due | List | Trello ID |
|------|-----|------|-----------|
| Add repo-level COE slash command (`.claude/commands/coe.md`) | Apr 1 | This Sprint | 69ab71ed2c1040f2f018f3a0 |
| COE audit: verify request sanitization (5 items) | Apr 1 | This Sprint | 69c45bd9b86b2636a37636ec |
| COE audit: known-bad pattern regression tests | Apr 8 | This Sprint | 69c45bdf0da2c400f1c87897 |
| COE audit: passthrough fallback on pipeline failures | Apr 8 | This Sprint | 69c45bdce86d1633fb11e52f |
| COE audit: graceful degradation for corrupted streams | Apr 8 | This Sprint | 69c45be1d3bd221e90816a91 |
| Mock mode: route through policy pipeline via MockAnthropicClient | — | This Sprint | 69bbf6aa63f8ae6e66587093 |
| Merge ready PRs batch (Paolo's batch incl. PR #335) | — | This Sprint | 69bffa511ed69d37cf09dc87 |
| Server-side LLM API credential management | — | Top Priority | 69c41048e5f42e83ab3d2917 |

---

## Deferred / Blocked

- **Non-localhost admin protection test**: Deferred — requires adding `localhost_auth_bypass` to `PUT /api/admin/gateway/settings` endpoint first (PR #405 prerequisite)
- **Sentry org setup**: "Set up Luthien org Sentry account" + "Share Sentry endpoint access with Paolo" — both in Backlog
- **PR #335 Sentry**: Waiting on Peter re-review

---

## Next Recommended Actions (in order)

1. **Draft Slack message** for Jai thread (Scott's ask — making the case for mock tests)
2. **Resolve conflicts** in PR #408 (rebase on main)
3. **Split PR #408** into PR A (CI job), PR B (failure script), PR C (fix + test pairs)
4. **COE slash command** — quick win, due Apr 1
5. **COE audit items** — due Apr 1–8
