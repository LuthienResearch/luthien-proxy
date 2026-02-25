# Feb 26: What's Our Limiting Factor?

**Date:** 2026-02-26
**Author:** Scott Wofford
**Audience:** Jai, Finn & Esben
**References:** [Feb 19 Uber Requirements](https://hackmd.io/@scwoff/HkwCxCVuZg), [Shipping Log](https://hackmd.io/@scwoff/BJH56Y3_Ze)

---

## The Answer

**Our limiting factor is the gap between "infrastructure built" and "value demonstrated."**

We have composable policies, passthrough auth, a policy config UI, conversation live view, Railway deployment, and a polished README with before/after SVGs. But **zero people** — including us — have experienced Luthien doing something *actually useful* in their daily work.

Every output metric we care about (paying customers, "sleep at night" quote, design partner retention) requires someone to experience value. That hasn't happened.

---

## Why It Hasn't Happened (3 layers)

### Layer 1: No "killer app" policy exists

Caps lock works for demos. But from last week's meeting: *"I have yet to use for myself or demonstrate to another human an actually useful policy."*

The infrastructure can support useful policies — but nobody has built one yet.

**Evidence:**
- `policy_config.yaml` still defaults to NoOpPolicy
- 15+ user interviews surfaced concrete policy ideas (Nico ranked 8), but none built
- Policy brainstorm didn't start until today (Feb 25)

### Layer 2: Setup still breaks on first contact

Every dogfooding session surfaces new bugs. On Feb 23, Scott hit a 401 error within 5 minutes of fresh setup (auth_mode default mismatch — PR #222). Jai's OAuth fixes (#214, #219, #221) addressed the underlying auth architecture this week, which is real progress. But the pattern persists: we discover bugs faster than we fix them.

**Feb 19 uber requirement #1 was "Better than no proxy" (Red).** After this week's auth fixes, it's closer to Yellow, but unproven without sustained dogfooding.

### Layer 3: EAG pipeline is decaying

15+ EAG conversations generated genuine enthusiasm 10-13 days ago. Trello "In progress" has ~15 debrief items (Diogo, Luis, Martin, Dylan, Max, Lindley, Prakrak, Mike M...). Some follow-up actions exist (Diogo README share, Marius/Jai meeting setup) but it's unclear how many have converted to booked calls. Tyler/Redwood — our warmest lead — hasn't progressed past the Feb 10 demo.

---

## The Dependency Chain

```
No useful policy → Can't dogfood meaningfully → Can't prove stability
                                                        ↓
Can't prove stability → Can't demo to EAG contacts → Pipeline decays
                                                        ↓
Pipeline decays → 0 paying customers → Demo Day at risk
```

**You can't convert leads without something to show. You can't show something that works without dogfooding. You can't dogfood meaningfully without a policy worth using.**

The leverage point is at the top: **build one useful policy.**

---

## What This Week Looked Like

| Activity | ~Effort | Moves bottleneck? |
|----------|---------|-------------------|
| README/SVG value-prop rewrite (56 commits on PR #179) | ~60% | No — polishing storefront when store is empty |
| Auth bug dogfooding + PR #222 | ~10% | **Yes** — directly feeds stability |
| Housekeeping PRs (shellcheck, test model, /coe) | ~10% | Marginal — useful but not urgent |
| QA Upwork instructions page | ~5% | Premature — no policy to test yet |
| EAG debrief items (writing notes) | ~5% | No — notes aren't booked calls |
| Policy brainstorm | Started today | **Yes** — this IS the bottleneck |

**Honest assessment:** Most of the week went to README polish, which is a classic perfectionism trap. The README looks great, but it describes value we haven't proven yet. The auth fixes from both Jai and Scott are real progress on stability.

---

## Proposed Focus for Next Week

### Primary: Get one useful policy running. Dogfood it daily.

Candidate policies (from user interviews):
1. **Commit message quality** — enforce conventional commits, flag overly large diffs
2. **CLAUDE.md compliance** — check if agent follows project-specific rules
3. **"Logged by Luthien" indicator** — simplest: append monitoring notice to system prompt
4. **Code style enforcement** — catch AI-isms, enforce project conventions (DeSlop already exists)

**Success criteria:** Scott and Jai both use a real policy in every coding session for 5 consecutive days. If it breaks, fix it. If it's annoying, tune it. If it's useful, demo it to the warmest EAG contact.

### Secondary: Convert EAG leads before they go cold

- Book 5 follow-up calls this week
- But only AFTER you can show them something in the call

### Tertiary: Merge and move on

- Merge README PR #179 as-is, stop iterating
- Blog post if time allows

---

## Decisions for the Group

1. **Is "build one useful policy" the right bottleneck to break?** Or should we prioritize pure stability (bug fixing with NoOp) first?
2. **Which policy?** Scott + Jai should pick one by end of this call.
3. **Should Scott stop the README work?** PR #179 has 56 commits and isn't merged. Merge as-is or keep iterating?
4. **EAG follow-ups timing:** Book now (risk showing something broken) or wait until policy exists (risk leads going cold)?

---

## Appendix A: Last Week's Goals Scorecard

| Goal | Target | Result | |
|------|--------|--------|--|
| EAG SF follow-ups | 5+ meetings by Feb 27 | Some follow-up actions (Diogo, Marius), unclear how many calls booked | ⚠️ |
| Hire Upwork QA engineer | Hired and running | Instructions page drafted, no hire | ❌ |
| Tyler's team deployment | 2nd user in logs | No visible progress | ❌ |
| Yoeri BD advisor decision | Decision made | "Add to Seldon agenda" in Trello — undecided | ❌ |
| Dogfooding + bug fixing | 8hrs zero failures | 1 session (~1hr), found auth bug → PR #222 | ⚠️ |
| Publish 1 blog post | Published | Not published | ❌ |

**Pattern:** The week's actual output (README polish) wasn't on the goal list. Real progress happened on stability (auth fixes), but stated goals mostly didn't move.

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
