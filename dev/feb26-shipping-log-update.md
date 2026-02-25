## This Week (Feb 19 - Feb 25)

**Date:** Feb 25

**Company:** Luthien

---

### What did you ship last week?

1. **Jai: OAuth auth chain complete** — Bearer token passthrough (#221), forwarding (#219), all-endpoint auth (#214). Claude Code relay works without workarounds.
2. **Scott: README/value-prop overhaul** — 56 commits on PR #179. Before/After SVG diagrams, simplified Quick Start, Tyler + Jai feedback incorporated. Not yet merged.
3. **Scott: Dogfood bug fix + housekeeping** — Auth_mode default fix (#222, RCA/COE), shellcheck (#224), test model constant (#226), /coe command (#227), TODO cleanup (#209).

---

### What are your goals for next week?

1. **Get one useful policy running.** Binary: policy loaded in config, used in 5+ coding sessions by Friday.
2. **Book 5 EAG follow-up calls.** Binary: 5 calls on calendar by Friday Mar 6.
3. **Merge README PR #179.** Binary: merged to main.

---

### PRs This Week (Feb 19 - Feb 25)

| Date | What | PR | Who | Status |
|------|------|-----|-----|--------|
| Feb 19 | Railway deploy failures fix | [#212](https://github.com/LuthienResearch/luthien-proxy/pull/212) | Jai | ✅ Merged |
| Feb 19 | Codebase cleanup | [#211](https://github.com/LuthienResearch/luthien-proxy/pull/211) | Jai | ✅ Merged |
| Feb 19 | StringReplacementPolicy test fix | [#213](https://github.com/LuthienResearch/luthien-proxy/pull/213) | Jai | ✅ Merged |
| Feb 19 | Bearer tokens + x-api-key auth | [#214](https://github.com/LuthienResearch/luthien-proxy/pull/214) | Jai | ✅ Merged |
| Feb 19 | Bypass nesting detection in e2e | [#215](https://github.com/LuthienResearch/luthien-proxy/pull/215) | Jai | ✅ Merged |
| Feb 19 | Replace last prod assert | [#216](https://github.com/LuthienResearch/luthien-proxy/pull/216) | Jai | ✅ Merged |
| Feb 19 | Railway demo updates | [#217](https://github.com/LuthienResearch/luthien-proxy/pull/217), [#220](https://github.com/LuthienResearch/luthien-proxy/pull/220) | Jai | ✅ Merged |
| Feb 19 | Forward OAuth bearer tokens | [#219](https://github.com/LuthienResearch/luthien-proxy/pull/219) | Jai | ✅ Merged |
| Feb 19 | Default AUTH_MODE to both | [#206](https://github.com/LuthienResearch/luthien-proxy/pull/206) | Scott | ✅ Merged |
| Feb 19 | Static file cache + TODO cleanup | [#207](https://github.com/LuthienResearch/luthien-proxy/pull/207) | Scott | ✅ Merged |
| Feb 19 | Remove 19 completed TODO items | [#209](https://github.com/LuthienResearch/luthien-proxy/pull/209) | Scott | ✅ Merged |
| Feb 23 | **OAuth bearer token passthrough** | [#221](https://github.com/LuthienResearch/luthien-proxy/pull/221) | Jai | ✅ Merged |
| Feb 19–25 | **README/value-prop rewrite** (56 commits) | [#179](https://github.com/LuthienResearch/luthien-proxy/pull/179) | Scott | 🔵 Open |
| Feb 24 | Auth_mode default fix + RCA/COE | [#222](https://github.com/LuthienResearch/luthien-proxy/pull/222) | Scott | 🔵 Open |
| Feb 24 | Shellcheck integration | [#224](https://github.com/LuthienResearch/luthien-proxy/pull/224) | Scott | 🔵 Open |
| Feb 24 | DEFAULT_CLAUDE_TEST_MODEL constant | [#226](https://github.com/LuthienResearch/luthien-proxy/pull/226) | Scott | 🔵 Open |
| Feb 24 | Repo-level /coe slash command | [#227](https://github.com/LuthienResearch/luthien-proxy/pull/227) | Scott | 🔵 Open |
| Feb 19 | Deduplicate tools before API call | [#208](https://github.com/LuthienResearch/luthien-proxy/pull/208) | Scott | 🔵 Open |
| Feb 17 | Empty text content blocks fix | [#201](https://github.com/LuthienResearch/luthien-proxy/pull/201) | Scott | 🔵 Open |

**Summary:** 12 merged (9 Jai, 3 Scott), 7 open. Jai's week focused on completing OAuth auth chain. Scott's week was mostly README polish + 1 dogfood bug fix + housekeeping.
