## This Week (Feb 19 - Feb 25)

**Date:** Feb 25

**Company:** Luthien

---

### What did you ship last week?

1. 🔥🎉 **Jai: Closed out the auth chain (continuation of last week's massive infra push)** — On Thursday alone (Feb 19), Jai shipped 8 PRs: Railway deploy fixes (#212), codebase cleanup (#211), auth on all endpoints (#214), OAuth forwarding (#219), plus test fixes and refactoring (#213, #215, #216, #217). Then one final OAuth passthrough PR (#221) over the weekend before getting sick Monday. Combined with last week's composable policies, config UI, and live view — Claude Code relay now works end-to-end without workarounds. 🚀 The #1 technical blocker from Feb 19 is resolved.
2. 🧹✨ **Scott: README/landing page overhaul incorporating Tyler + Jai feedback** — PR #179 (56 commits, workshopping with Tyler async before next week's call). Before/After SVG diagrams showing real Claude Code UX, simplified Quick Start, question-based headers. Applied Musk 5-step design process to cut unnecessary sections. Extracted and addressed Tyler's specific feedback from Feb 10 live install. Jai reviewing. This feeds directly into QA hiring pipeline (Trello: "update readme → hire upwork people" ✅ Done).
3. **Scott: BD + dogfooding + dev tooling:**
   - **Tyler/Redwood follow-up:** Prepped for call, Tyler will review landing page async and meet next week (Jai was sick this week). [Trello card created](https://trello.com/c/q0dqTgxo) with today's deadline.
   - **Dogfooding:** Found auth_mode default bug in 5 min of fresh setup → [PR #222](https://github.com/LuthienResearch/luthien-proxy/pull/222) with full RCA/COE + guard test + startup warning
   - **QA hiring pipeline:** Drafted Upwork trial task. **Peter (CTO, QADNA) already completed his trial** — [QA Trial Report](https://docs.google.com/document/d/1xugPuJjtfxXw3ale54rdhqAlsH5wcvo31RtPVPJLgz4/edit) + [Loom video](https://www.loom.com/share/c4e1b1ef83224dcca9420de8a448d846). Found 9 bugs (including critical Activity Monitor silent failure), 4 onboarding issues, validated our value prop. [Full debrief](dev/peter-qadna-qa-trial-debrief.md).
   - **BD:** Engaged LiteLLM sales team, prepped for Govind meeting, reached out to Eric Liu (PBC lawyer), Seldon weekly prep
   - **Housekeeping PRs:** shellcheck integration ([#224](https://github.com/LuthienResearch/luthien-proxy/pull/224)), DEFAULT_CLAUDE_TEST_MODEL constant ([#226](https://github.com/LuthienResearch/luthien-proxy/pull/226)), repo-level /coe command ([#227](https://github.com/LuthienResearch/luthien-proxy/pull/227)), TODO cleanup ([#209](https://github.com/LuthienResearch/luthien-proxy/pull/209)), deduplicate tools fix ([#208](https://github.com/LuthienResearch/luthien-proxy/pull/208))
   - **Policy brainstorming:** Started researching which policy to dogfood (session logs + user interview synthesis)

#### Peter/QADNA QA Trial — Key Takeaways

- **Who:** Peter, CTO of QADNA (Bucharest). Ex-Google, runs 14 Claude Code instances daily, building AI-driven QA SaaS
- **Verdict:** *"I haven't seen something like this before... this is something that should exist"* — wants to continue toward April demo day
- **9 bugs found**, most critical: Activity Monitor completely non-functional (silent `emitter.py` failure — events never reach Redis). Also: Diff Viewer 404, conversation history empty, DeSlop activation error, SamplePydanticPolicy crashes everything
- **4 onboarding issues:** Python 3.13+ not checked, no OAuth/Max guidance, AUTH_MODE DB override (our known #222), unclear API key errors
- **5 frustrations added to DB (#55-59):** unsupervised agent drift (8-9/10), destructive actions fear (~10/10), token costs, processes die after 10hrs, context loss after compacting
- **Landing page feedback:** "Very clear", "love how it looks", dark mode appreciated
- **Action items:** File bugs, review Peter's emitter.py fix, evaluate QADNA as QA partner, follow up on engagement
- **Requirements impact:** Activity Monitor bug means uber req #1 is worse than we thought. Policy config UI gap confirms uber req #3 gap.

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

**Summary:** 12 merged (9 Jai, 3 Scott), 7 open. Story arc: Last week was Jai's massive infra push (composable policies, auth, config UI, live view). This week he closed out the remaining auth chain in a single Thursday sprint (8 PRs), then was sick. Scott's week was about preparing to put that infrastructure in front of users — landing page overhaul incorporating design partner feedback, Tyler follow-up, QA hiring pipeline, BD, dogfooding, and starting policy brainstorming.

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
