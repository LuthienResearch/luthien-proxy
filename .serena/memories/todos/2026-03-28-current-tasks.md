# Current Tasks — 2026-03-28 (updated end of day)

## Context
- Demo Day: April 16, 2026 (T-19 days as of Mar 28)
- Active branches: `e2e-mock-ci`, `e2e-owasp-tests`, `e2e-failure-capture`

---

## Open PRs — Status

### PR #457 — ci: add mock-backend e2e job to CI
- **Branch**: `e2e-mock-ci` → base: `e2e-owasp-tests` (retargeted, merges after #458)
- **State**: OPEN, all review items addressed
- **Waiting on**: Jai merge after #458 lands

### PR #458 — test: add OWASP LLM01/06/08 mock e2e test suite
- **Branch**: `e2e-owasp-tests` → base: `main`
- **State**: OPEN, multiple rounds of review addressed
- **Review history**: 3 rounds from Jai, all items fixed
- **Waiting on**: Jai final approval

### PR #459 — feat: real-API failure capture → deterministic mock regression pipeline
- **Branch**: `e2e-failure-capture` → base: `e2e-owasp-tests`
- **State**: OPEN, Claude bot + Jai review items addressed
- **Waiting on**: Jai review after #458 merges

### PR #335 — Sentry integration
- **State**: OPEN, awaiting Peter re-review after rebase
- **Not on any of the above branches**

---

## NEXT: Draft Slack Message to Jai

Scott asked Paolo to make the case for mock tests in the existing Slack thread.
Jai's concern: "AI control tests vs functional tests" framing.

**Key points to make:**
1. Tests are deterministic (mock server, no real API calls, no cost)
2. They test POLICY ORCHESTRATION — does the gateway actually wire up the judge, parse response, and act on it? That's functional behavior, not AI behavior
3. The real-API tests (PR #459) handle the "does the judge actually work" question
4. Split into 3 focused PRs per Jai's own request — now easy to review independently

**Slack convention**: First message 1-2 lines + :thread: emoji, details in thread reply.

---

## Trello Action Items (Paolo-relevant, next up)

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

## Work Done Today (2026-03-28)

- Closed PR #408, split into PRs #457, #458, #459
- Addressed 3+ rounds of Jai review on #457 and #458:
  - Fixed CI job retargeting (#457 → e2e-owasp-tests base)
  - Fixed changelog fragments (rename, pr field, category)
  - Replaced `_HEADERS`/`_BASE_REQUEST` locals with shared `MOCK_HEADERS`/`BASE_REQUEST`
  - Added `.gitignore` for `failure_registry/*.json` and `.serena/`
  - Fixed `_reset_mock_server` and `collect_sse_text` docstrings
  - Removed `_DOGFOOD_SAFETY` duplicate in audit trail
  - Fixed test count in changelog
  - Bumped poll timeout 5s→10s
- Addressed Claude bot review on #459:
  - Empty `expected` → `pytest.skip()` instead of vacuous assertion
  - `monkeypatch.setattr` instead of unsafe try/finally global mutation
  - Security docstring on `_render_test`
  - Added 4 new test cases: adversarial strings, missing policy_config, compile check, skip generation
- Added `FailureCapture.reset()` method + retry dedup fix
- 21 unit tests for `generate_mock_from_failures.py`

---

## Deferred

- Non-localhost admin protection test (needs PR #405 fix first)
- Sentry org setup (Backlog)
