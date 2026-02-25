## This Week (Feb 19 - Feb 25)

**Date:** Feb 25

**Company:** Luthien

---

### What did you ship last week?

1. **Jai: OAuth auth chain complete** — Bearer token passthrough (#221), OAuth forwarding (#219), auth on all endpoints (#214), Railway deploy fixes (#212), codebase cleanup (#211). Claude Code relay now works without workarounds — this was the #1 technical blocker from Feb 19.
2. **Scott: README/landing page overhaul incorporating Tyler + Jai feedback** — PR #179 (56 commits, workshopping with Tyler async before next week's call). Before/After SVG diagrams showing real Claude Code UX, simplified Quick Start, question-based headers. Applied Musk 5-step design process to cut unnecessary sections. Extracted and addressed Tyler's specific feedback from Feb 10 live install. Jai reviewing. This feeds directly into QA hiring pipeline (Trello: "update readme → hire upwork people" ✅ Done).
3. **Scott: BD + dogfooding + dev tooling:**
   - **Tyler/Redwood follow-up:** Prepped for call, Tyler will review landing page async and meet next week (Jai was sick this week)
   - **Dogfooding:** Found auth_mode default bug in 5 min of fresh setup → PR #222 with full RCA/COE + guard test + startup warning
   - **QA hiring pipeline:** Updated QA instructions page on scottwofford.com, drafted Upwork trial task message
   - **BD:** Engaged LiteLLM sales team, prepped for Govind meeting, reached out to Eric Liu (PBC lawyer), Seldon weekly prep
   - **Housekeeping PRs:** shellcheck integration (#224), DEFAULT_CLAUDE_TEST_MODEL constant (#226), repo-level /coe command (#227), TODO cleanup (#209), deduplicate tools fix (#208)
   - **Policy brainstorming:** Started researching which policy to dogfood (session logs + user interview synthesis)

---

### What are your goals for next week?

1. **Get one useful policy running.** Binary: policy loaded in config, used in 5+ coding sessions by Friday.
2. **Tyler call + landing page review.** Binary: call happens, Tyler gives feedback on async review.
3. **Book 5 EAG follow-up calls.** Binary: 5 calls on calendar by Friday Mar 6.

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
| Feb 19–25 | **README/landing page rewrite** (56 commits, Tyler + Jai feedback) | [#179](https://github.com/LuthienResearch/luthien-proxy/pull/179) | Scott | 🔵 Open (workshopping w/ Tyler) |
| Feb 24 | Auth_mode default fix + RCA/COE | [#222](https://github.com/LuthienResearch/luthien-proxy/pull/222) | Scott | 🔵 Open |
| Feb 24 | Shellcheck integration | [#224](https://github.com/LuthienResearch/luthien-proxy/pull/224) | Scott | 🔵 Open |
| Feb 24 | DEFAULT_CLAUDE_TEST_MODEL constant | [#226](https://github.com/LuthienResearch/luthien-proxy/pull/226) | Scott | 🔵 Open |
| Feb 24 | Repo-level /coe slash command | [#227](https://github.com/LuthienResearch/luthien-proxy/pull/227) | Scott | 🔵 Open |
| Feb 19 | Deduplicate tools before API call | [#208](https://github.com/LuthienResearch/luthien-proxy/pull/208) | Scott | 🔵 Open |
| Feb 17 | Empty text content blocks fix | [#201](https://github.com/LuthienResearch/luthien-proxy/pull/201) | Scott | 🔵 Open |

**Summary:** 12 merged (9 Jai, 3 Scott), 7 open. Jai completed the OAuth auth chain (biggest blocker). Scott: landing page overhaul incorporating design partner feedback, dogfood bug fix, QA hiring pipeline, BD follow-ups, policy brainstorming started.

---

### Trello Done This Week

| Date | Card |
|------|------|
| Feb 25 | prep for call with Tyler |
| Feb 25 | update readme → hire upwork people |
| Feb 22 | close tabs and windows |
| Feb 20 | Prep for Seldon weekly mtg |
| Feb 20 | book hotel for SF |
| Feb 20 | prep for Govind meeting |
| Feb 20 | prepare for Seldon mtg today |
| Feb 20 | Engage LiteLLM sales team |
| Feb 20 | EAG SF Outreach |
| Feb 20 | Reach out to Eric Liu (PBC lawyer) |
