# Feb 26: What's Our Limiting Factor?

**Date:** 2026-02-26

**Author:** Scott Wofford

**For Discussion with:** Jai, Finn & Esben

**Sources:** [Feb 19 Uber Requirements](https://hackmd.io/@scwoff/HkwCxCVuZg), [Shipping Log](https://hackmd.io/@scwoff/BJH56Y3_Ze), [Last Week's Check-in](#appendix-a-last-weeks-goals-scorecard)

---

Since the Feb 19 meeting, Jai completed the OAuth auth chain ([#214](https://github.com/LuthienResearch/luthien-proxy/pull/214), [#219](https://github.com/LuthienResearch/luthien-proxy/pull/219), [#221](https://github.com/LuthienResearch/luthien-proxy/pull/221)) — the #1 technical blocker we identified. Claude Code relay now works without workarounds. I rewrote the README value prop with before/after SVG diagrams ([PR #179](https://github.com/LuthienResearch/luthien-proxy/pull/179), 56 commits, not yet merged) and found another dogfooding bug within 5 minutes of fresh setup: the auth_mode default was seeded wrong in the DB migration ([PR #222](https://github.com/LuthienResearch/luthien-proxy/pull/222), includes RCA/COE and guard test). Infrastructure-wise, we're in a stronger position than last week.

However, I scored 0 of 6 on last week's goals (see [Appendix A](#appendix-a-last-weeks-goals-scorecard)). No EAG follow-up calls booked. No QA engineer hired. No blog post published. Tyler/Redwood hasn't progressed past the Feb 10 demo. Most of my time (~60%) went to README polish — which was not on the goal list. Meanwhile, our 15+ EAG leads are now 10-13 days old and cooling. And from last week's meeting, the thing that stuck with me: *"I have yet to use for myself or demonstrate to another human an actually useful policy."*

**Central question:** What is our current limiting factor, and what should we focus on to break through it?

**Scott's Tentative Answer (for discussion):** The limiting factor is the gap between "infrastructure built" and "value demonstrated." We have composable policies, passthrough auth, a config UI, conversation live view, Railway deployment, and a polished README. But zero people — including us — have experienced Luthien doing something *actually useful*. Everything downstream (conversions, revenue, "sleep at night" quote) is blocked on this. I propose we focus on three things in priority order:

1. **Build one useful policy** and dogfood it daily
2. **Prove stability** through sustained dogfooding (Feb 19's uber requirement #1)
3. **Convert EAG leads** before they go cold — but only once we have something to show

---

## 1. No Useful Policy Exists

We have infrastructure that can support useful policies — composable policies ([#184](https://github.com/LuthienResearch/luthien-proxy/pull/184)), dynamic config UI ([#175](https://github.com/LuthienResearch/luthien-proxy/pull/175)), streaming pipeline, judge policies. But `policy_config.yaml` still defaults to NoOpPolicy. Nobody has built a policy that solves a real developer pain point.

**Status: Red.** This is the bottleneck.

**Evidence:**
- 15+ user interviews have surfaced concrete policy ideas (Nico alone ranked 8), but none have been implemented
- Caps lock demos worked ~75% at EAG SF, but no design partner would use it day-to-day
- Policy brainstorm didn't start until today (Feb 25)
- Without a useful policy, dogfooding is just testing plumbing — necessary, but not sufficient to prove value

**Why this matters:** Every output metric (paying customers, design partner retention, "sleep at night" quote) requires someone to experience value. A polished README, a working auth chain, and 15 enthusiastic EAG conversations are all upstream investments. They only pay off when someone runs a policy that makes their workflow better.

**Goal:** By Mar 7, Scott and Jai are both running one useful policy in every coding session. If it breaks, we fix it. If it's annoying, we tune it. If it's useful, we demo it.

**Candidate policies (from user interviews + dogfooding ideas):**

| Policy | Source | Complexity | Dogfood value |
|--------|--------|-----------|---------------|
| Commit message quality | Jai suggestion, Nico #3 | Medium | High — we'd use it daily |
| CLAUDE.md compliance check | Nico #1, Tyler feedback | Medium-High | High — catches the "slop PR" pattern |
| "Logged by Luthien" indicator | Scott TODO | Low | Low — visibility only, no enforcement |
| Code style / DeSlop | EAG demos, existing policy | Already built | Medium — needs tuning for real use |
| No silent failures | Dogfooding pain (PR #204 lesson) | Medium | High — directly addresses Jai's feedback |

**Path to green:**
1. Pick one policy by end of this call (Owner: Scott + Jai)
2. Build or configure it this week (Owner: TBD)
3. Both dogfood it for 5 consecutive days (Owner: Scott + Jai)
4. Demo to warmest EAG contact if it works (Owner: Scott)

---

## 2. Stability Is Improving but Unproven

Feb 19's uber requirement #1 ("Better than no proxy") was Red. This week's auth fixes (#214, #219, #221, #222) addressed the biggest known blocker — Claude Code OAuth relay. But we haven't sustained enough dogfooding to know if the remaining bugs table from Feb 19 is clear.

**Status: Yellow (up from Red).** Auth architecture resolved; remaining bugs unverified.

| What broke | Feb 19 Status | Current Status |
|-----------|--------------|----------------|
| `cache_control` on tools rejected | [#178](https://github.com/LuthienResearch/luthien-proxy/pull/178) open | 🔵 Still open |
| Empty text content blocks | [#201](https://github.com/LuthienResearch/luthien-proxy/pull/201) open | 🔵 Still open |
| Claude Code auth failure (OAuth) | [#205](https://github.com/LuthienResearch/luthien-proxy/issues/205) | ✅ Fixed (#214, #219, #221, #222) |
| `/compact` duplicate tools | [#208](https://github.com/LuthienResearch/luthien-proxy/pull/208) open | 🔵 Still open |

Three of four known bugs still have open PRs. Jai needs to review #178, #201, #208 to close them.

**Goal:** Same as Feb 19 — 8 hours of real Claude Code work through the proxy with zero proxy-caused failures.

**Path to green:**
1. Jai reviews + merges remaining bug fix PRs (#178, #201, #208) (Owner: Jai)
2. Scott dogfoods daily on NoOpPolicy until clean, then switches to useful policy (Owner: Scott)
3. If new bugs surface, fix them same-day — don't let them accumulate (Owner: whoever finds it)

---

## 3. EAG Pipeline Is Decaying

15+ EAG conversations generated genuine enthusiasm 10-13 days ago. Trello "In progress" currently has ~15 debrief items: Diogo (AE Studio), Luis (Equistamp), Martin (AE Studio), Dylan Fridman, Max Werner, Lindley, Prakrak, Mike M, and others. Some follow-up actions exist (send Diogo the README, set up Jai/Marius call), but it's unclear how many have converted to booked second meetings.

Tyler/Redwood — our warmest lead and the closest thing to a "sleep at night" quote — hasn't progressed since the Feb 10 demo. The auth fixes this week should unblock his team technically, but nobody has reached out to confirm.

**Status: Yellow.** Pipeline exists but momentum is fading. Last week's goal was "5+ follow-up meetings by Feb 27" — unclear if we'll hit it.

**The tension:** We need to follow up before leads go cold, but following up with a broken or useless product wastes their goodwill. This is why building a useful policy (section 1) comes first — even a 1-week delay in follow-ups is worth it if we can show something real.

**Path to green:**
1. Build useful policy first (this week) — gives us something to show in follow-up calls
2. Book 5 follow-up calls for next week (Owner: Scott, by Mar 3)
3. Tyler/Redwood: confirm auth fixes unblock their setup, schedule check-in (Owner: Scott)
4. Merge README PR #179 before follow-up calls — it's ready, stop polishing (Owner: Scott)

---

## Decisions Needed

1. **Is "build one useful policy" the right bottleneck?**
   Or should we prioritize pure stability (fix remaining bugs on NoOp) before adding policy complexity? My argument: we need both, but the policy is the leverage point because it gives us a reason to dogfood AND something to demo.

2. **Which policy?**
   Scott + Jai should pick one by end of this call. See candidates table in Section 1.

3. **Merge README PR #179 as-is?**
   56 commits, not yet merged. It's good. Let's merge and move on. The perfectionism pattern is clear — I spent ~60% of this week on it instead of my stated goals.

4. **EAG follow-ups: now or after policy?**
   Risk of leads cooling (already 10-13 days) vs. risk of showing something broken. My recommendation: build policy this week, book calls for next week.

---

## Appendix A: Last Week's Goals Scorecard

| Goal | Target | Result | |
|------|--------|--------|--|
| EAG SF follow-ups | 5+ meetings by Feb 27 | Some follow-up actions in Trello, unclear how many calls booked | ⚠️ |
| Hire Upwork QA engineer | Hired and running | Instructions page drafted, no hire | ❌ |
| Tyler's team deployment | 2nd user in logs | No visible progress | ❌ |
| Yoeri BD advisor decision | Decision made | "Add to Seldon agenda" in Trello — undecided | ❌ |
| Dogfooding + bug fixing | 8hrs zero failures | 1 session (~1hr), found auth bug → PR #222 | ⚠️ |
| Publish 1 blog post | Published | Not published | ❌ |

**What actually happened:** ~60% of the week went to README/SVG polish (not on goal list). ~10% to dogfooding (found real bug, good). ~15% to housekeeping PRs. ~5% to QA prep. Policy brainstorm started today.

**Pattern:** The README work has value — it will help with conversions. But the week's biggest investment wasn't on the goal list, and it polishes the storefront while the store is empty. This is the perfectionism/scope-creep pattern we've identified before.

## Appendix B: PRs This Week (Feb 19-25)

### Jai (9 PRs merged)

| Date | What | PR | Status |
|------|------|----|--------|
| Feb 19 | Railway deploy failures fix | [#212](https://github.com/LuthienResearch/luthien-proxy/pull/212) | ✅ Merged |
| Feb 19 | Codebase cleanup — dead code, fail-fast, dedup | [#211](https://github.com/LuthienResearch/luthien-proxy/pull/211) | ✅ Merged |
| Feb 19 | StringReplacementPolicy test fix | [#213](https://github.com/LuthienResearch/luthien-proxy/pull/213) | ✅ Merged |
| Feb 19 | Bearer tokens + x-api-key auth | [#214](https://github.com/LuthienResearch/luthien-proxy/pull/214) | ✅ Merged |
| Feb 19 | Bypass nesting detection in e2e | [#215](https://github.com/LuthienResearch/luthien-proxy/pull/215) | ✅ Merged |
| Feb 19 | Replace last prod assert with static types | [#216](https://github.com/LuthienResearch/luthien-proxy/pull/216) | ✅ Merged |
| Feb 19 | Railway demo updates | [#217](https://github.com/LuthienResearch/luthien-proxy/pull/217), [#220](https://github.com/LuthienResearch/luthien-proxy/pull/220) | ✅ Merged |
| Feb 19 | Forward OAuth bearer tokens | [#219](https://github.com/LuthienResearch/luthien-proxy/pull/219) | ✅ Merged |
| Feb 23 | **OAuth bearer token passthrough** | [#221](https://github.com/LuthienResearch/luthien-proxy/pull/221) | ✅ Merged |

### Scott (3 merged, 7 open)

| Date | What | PR | Status |
|------|------|----|--------|
| Feb 19 | Default AUTH_MODE to both | [#206](https://github.com/LuthienResearch/luthien-proxy/pull/206) | ✅ Merged |
| Feb 19 | Static file cache + TODO cleanup | [#207](https://github.com/LuthienResearch/luthien-proxy/pull/207) | ✅ Merged |
| Feb 19 | Remove 19 completed TODO items | [#209](https://github.com/LuthienResearch/luthien-proxy/pull/209) | ✅ Merged |
| Feb 19–25 | **README/value-prop rewrite** (56 commits) | [#179](https://github.com/LuthienResearch/luthien-proxy/pull/179) | 🔵 Open |
| Feb 24 | Auth_mode default fix (dogfood bug) + RCA/COE | [#222](https://github.com/LuthienResearch/luthien-proxy/pull/222) | 🔵 Open |
| Feb 24 | Shellcheck integration to dev_checks | [#224](https://github.com/LuthienResearch/luthien-proxy/pull/224) | 🔵 Open |
| Feb 24 | DEFAULT_CLAUDE_TEST_MODEL constant | [#226](https://github.com/LuthienResearch/luthien-proxy/pull/226) | 🔵 Open |
| Feb 24 | Repo-level /coe slash command | [#227](https://github.com/LuthienResearch/luthien-proxy/pull/227) | 🔵 Open |
| Feb 19 | Deduplicate tools before API call | [#208](https://github.com/LuthienResearch/luthien-proxy/pull/208) | 🔵 Open |
| Feb 17 | Empty text content blocks fix | [#201](https://github.com/LuthienResearch/luthien-proxy/pull/201) | 🔵 Open |

**Summary:** Jai's week = auth architecture fixes (directly unblocking Claude Code usage). Scott's week = README polish + one dogfooding bug fix + housekeeping.
