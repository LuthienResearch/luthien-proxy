# Split APIs Implementation Notes

## Progress

| Step | Status | Notes |
|------|--------|-------|
| 1. Anthropic types + tests | Complete | TypedDicts for streaming events |
| 2. Anthropic SDK client + tests | Complete | 11 tests, 92% coverage |
| 3. Anthropic policy protocol + tests | Complete | 14 tests |
| 4. Anthropic NoOp policy + tests | Complete | 9 tests |
| 5. Anthropic stream executor + tests | Complete | 14 tests |
| 5b. Consolidate duplicate types | Complete | Fixed parallel agent coordination issue |
| 6. Wire gateway (e2e) | Complete | 18 unit + 9 integration tests |
| 7. AllCaps policy + tests | Not started | |
| 8. Migrate remaining policies | Not started | |
| 9. Clean up unused code | Not started | |

## Unexpected Problems

- 2026-02-03: Parallel agents (tasks 1 & 3) both defined streaming event TypedDicts in different files. Fixed by consolidating to `llm/types/anthropic.py`.

## Decisions Made

- 2026-02-03: Design approved. See `docs/plans/2026-02-03-split-apis-design.md`
- 2026-02-03: Use our own TypedDicts for streaming events (not SDK Pydantic types) for consistency and flexibility
- 2026-02-03: Canonical location for all Anthropic types is `llm/types/anthropic.py`

## Open Questions

(None yet)
