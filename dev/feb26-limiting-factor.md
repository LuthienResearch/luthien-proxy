# Feb 26: What's Our Limiting Factor?

**Date:** 2026-02-26

**Author:** Scott Wofford

**For Discussion with:** Jai, Finn & Esben

**Sources:** [Feb 19 Uber Requirements](https://hackmd.io/@scwoff/HkwCxCVuZg), [Shipping Log](https://hackmd.io/@scwoff/BJH56Y3_Ze), [Last Week's Check-in](#appendix-a-last-weeks-goals-scorecard)

---

[Last week](https://hackmd.io/@scwoff/BJH56Y3_Ze) was Jai's massive infrastructure push (composable policies, passthrough auth, config UI, conversation live view, deploy instructions). 🔥🎉 This week he closed out the remaining auth chain in a single Thursday sprint — 8 PRs in one day, plus a final OAuth fix over the weekend before getting sick Monday. Claude Code relay now works end-to-end without workarounds. 🚀 The #1 technical blocker from Feb 19 is resolved. Meanwhile, Scott rewrote the README/landing page incorporating Tyler + Jai feedback (workshopping with Tyler async before next week's call), found a dogfooding bug within 5 minutes of fresh setup, moved the QA hiring pipeline forward, and did BD work (LiteLLM, Govind, PBC lawyer). Infrastructure-wise, we're in a stronger position than last week. (Full PR list in [Appendix B](#appendix-b-prs-this-week-feb-19-25).)

But what should we work on next? Our Trello board has **~120 cards** across active lists, pulling in every direction at once. When I cross-cut them by theme rather than by Trello list and map each against our [three uber requirements from Feb 19](https://hackmd.io/@scwoff/HkwCxCVuZg), the problem becomes clear:

| Theme | # Cards | Uber Req Served |
|-------|---------|-----------------|
| EAG follow-ups & debriefs | ~20 | None directly |
| BD & partnerships | ~10 | None directly |
| Legal & admin | ~8 | None |
| **Product building** | **~15** | **#2 Time-to-value, #3 Policies** |
| **Dogfooding & QA** | **~8** | **#1 Stability, #3 Policies** |
| Content & comms | ~6 | None directly |
| Research & learning | ~8 | None |
| Feature ideas (someday/maybe) | ~35 | Mixed / future |
| Misc admin | ~10 | None |

Now map this against our three uber requirements:

| Uber Requirement (Feb 19) | Status then → now | Cards that advance it | Cards that don't |
|--------------------------|-------------------|----------------------|-----------------|
| **#1 "Better than no proxy"** (stability) | Red → **Yellow** | Dogfooding & QA (~8), bug fix PRs | ~112 |
| **#2 "Quick time-to-value"** (onboarding) | Yellow → **Yellow** | Product building subset (~5: onboarding, one-click deploy, README) | ~115 |
| **#3 "Policies that work when configured"** | Red → **Red** | Product building subset (~5: demo policies, Workflow Enforcement), Dogfooding (~3) | ~112 |

**The pattern: ~85% of our 120 cards don't directly advance any uber requirement.** Only ~23 are on the critical path — and the ones that matter *most right now* are the handful addressing uber requirement #3 (policies that actually work), because that's where the whole dependency chain is stuck. From last week's meeting, the thing that stuck with me: *"I have yet to use for myself or demonstrate to another human an actually useful policy."*

**Central question:** Given this overwhelming list, what is our current limiting factor — what one thing, if we got it right, would make the most other things either unnecessary or easier?

**Scott's Tentative Answer (for discussion):** The limiting factor is the gap between "infrastructure built" and "value demonstrated." We have composable policies, passthrough auth, a config UI, conversation live view, Railway deployment, and a polished README. But zero people — including us — have experienced Luthien doing something *actually useful*. Everything downstream (conversions, revenue, "sleep at night" quote) is blocked on this. Most of the 120 cards above are noise until we solve this. I propose we focus on three things in priority order:

1. **Build one useful policy** and dogfood it daily
2. **Prove stability** through sustained dogfooding (Feb 19's uber requirement #1)
3. **Convert EAG leads** before they go cold — but only once we have something to show

---

## 1. No Useful Policy Exists

We have the infrastructure (composable policies, config UI, streaming pipeline, judge policies) but `policy_config.yaml` still defaults to NoOpPolicy. 15+ user interviews surfaced concrete policy ideas, but none have been implemented. Without a useful policy, dogfooding is just testing plumbing and we have nothing to demo.

**Status: Red.** This is the bottleneck. Everything downstream (conversions, revenue, "sleep at night" quote) is blocked here.

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

15+ EAG conversations generated genuine enthusiasm 10-13 days ago. ~15 debrief items are sitting in Trello, but it's unclear how many have converted to booked second meetings. Tyler/Redwood — our warmest lead — is actively engaged (reviewing landing page async, meeting next week), but his team isn't using Luthien yet.

**Status: Yellow.** Tyler warm. Broader pipeline needs follow-up before leads go cold.

**The tension:** We need to follow up before leads go cold, but following up with a broken or useless product wastes their goodwill. This is why building a useful policy (section 1) comes first — even a 1-week delay in follow-ups is worth it if we can show something real.

**Path to green:**
1. Build useful policy first (this week) — gives us something to show in follow-up calls
2. Tyler/Redwood: call next week, confirm auth fixes unblock their setup (Owner: Scott)
3. Book 5 broader follow-up calls for next week (Owner: Scott, by Mar 3)
4. Finalize README PR #179 with Tyler's async feedback, then merge (Owner: Scott)

---

## Decisions Needed

1. **Is "build one useful policy" the right bottleneck?**
   Or should we prioritize pure stability (fix remaining bugs on NoOp) before adding policy complexity? My argument: we need both, but the policy is the leverage point because it gives us a reason to dogfood AND something to demo.

2. **Which policy?**
   Scott + Jai should pick one by end of this call. See candidates table in Section 1.

3. **README PR #179 — finalize after Tyler's async review?**
   56 commits incorporating Tyler + Jai feedback. Tyler is reviewing async this week. Finalize with his feedback, then merge before follow-up calls.

4. **EAG follow-ups: now or after policy?**
   Risk of leads cooling (already 10-13 days) vs. risk of showing something broken. My recommendation: build policy this week, book calls for next week.

---

## Appendix A: Last Week's Goals Scorecard

| Goal | Target | Result | |
|------|--------|--------|--|
| EAG SF follow-ups | 5+ meetings by Feb 27 | Some follow-up actions (Diogo, Marius/Jai meeting setup), unclear how many calls booked | ⚠️ |
| Hire Upwork QA engineer | Hired and running | QA instructions page updated, Upwork trial task drafted — pipeline moving, no hire yet | ⚠️ |
| Tyler's team deployment | 2nd user in logs | Tyler actively engaged — reviewing landing page async, call next week. Team not using yet | ⚠️ |
| Yoeri BD advisor decision | Decision made | "Add to Seldon agenda" in Trello — undecided | ❌ |
| Dogfooding + bug fixing | 8hrs zero failures | 1 session (~1hr), found auth bug → PR #222 with RCA/COE + guard test | ⚠️ |
| Publish 1 blog post | Published | Not published | ❌ |

**What actually happened:** README/landing page overhaul incorporating Tyler + Jai feedback (feeds QA hiring pipeline — Trello card Done). Dogfooding found real auth bug. QA pipeline moving. BD: LiteLLM sales, Govind prep, Eric Liu PBC lawyer. Tyler engaged async. Policy brainstorm started today. Jai closed out auth chain Thu + was sick Mon.

## Appendix B: PRs This Week (Feb 19-25)

**12 merged** (9 Jai, 3 Scott), **7 open** (all Scott, awaiting Jai review).

Highlights:
- **Jai:** Auth chain complete — bearer tokens (#214), OAuth forwarding (#219), final passthrough (#221). Plus deploy fixes, cleanup, and test hardening.
- **Scott:** README/landing page rewrite (#179, 56 commits, open — workshopping with Tyler). Auth bug fix (#222). Housekeeping: shellcheck, test constants, /coe command.
- **Awaiting review:** #178 (cache_control), #201 (empty text blocks), #208 (deduplicate tools) — all bug fixes blocking stability goal.

Full PR list in [shipping log](https://hackmd.io/@scwoff/BJH56Y3_Ze).
