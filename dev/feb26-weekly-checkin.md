# Weekly Check-in

**Date:** Feb 26

**Company:** Luthien

---

## What did you ship last week?

1. **Jai: OAuth auth chain complete** — Bearer token passthrough (#221), OAuth forwarding (#219), auth on all endpoints (#214). Claude Code relay now works without workarounds. This was the #1 technical blocker from Feb 19.
2. **Scott: README/value-prop overhaul** (PR #179, 56 commits, not yet merged) — Before/After SVG diagrams showing real Claude Code UX, simplified Quick Start, question-based headers. Incorporated Tyler + Jai feedback. Ready to merge.
3. **Scott: Dogfooding found + fixed auth_mode default bug** (PR #222 w/ full RCA/COE) — 5 minutes into fresh setup, hit 401. Root cause: DB migration seeded `proxy_key` but Claude Code sends OAuth tokens. Fix includes migration, startup warning, guard test. + 5 housekeeping PRs (#206, #207, #209, #224, #226, #227).

---

## What's blocking you right now?

**The blocker:** No useful policy exists. We have infrastructure for composable policies, auth, config UI, live view — but `policy_config.yaml` still defaults to NoOpPolicy. We can't dogfood meaningfully, demo value to leads, or convert anyone to a trial without a policy that solves a real pain point.

**Who can help?** Jai + Scott need to pick one policy and build it this week. Finn/Esben: help us decide which one (see [Limiting Factor doc](TBD_HACKMD_LINK)).

---

## What are your goals for next week?

### Scott's goals:

1. **Get one useful policy running end-to-end.** Binary: policy is loaded in config, tested, and used in 5+ coding sessions by Friday Mar 6.
2. **Book 5 EAG follow-up calls.** Binary: 5 calls on calendar by Friday. (15+ leads are 2 weeks old and decaying.)
3. **Merge README PR #179 and stop iterating.** Binary: merged to main.

### Jai's goals:

1. _(for Jai to fill in)_

---

## 📊 Batch North Star Metric

**Metric:** 3 paying customers by Demo Day (April 2026)

**Current status:** 0 paying. ~15 EAG leads in pipeline (aging). Tyler/Redwood warmest but stalled since Feb 10 demo. Auth blockers now resolved — but no useful policy to show anyone.

**On track?** ❌ — Pipeline exists but isn't being converted. The bottleneck isn't leads or infrastructure; it's that we have nothing useful to put in front of people yet. See [Limiting Factor analysis](TBD_HACKMD_LINK) for full breakdown.

---

## Scorecard: Last Week's Goals

| Goal | Target | Result | |
|------|--------|--------|--|
| EAG SF follow-ups | 5+ meetings by Feb 27 | Some actions taken (Diogo, Marius), unclear how many calls booked | ⚠️ |
| Hire Upwork QA | Hired | Instructions page drafted, no hire | ❌ |
| Tyler deployment | 2nd user in logs | No visible progress | ❌ |
| Yoeri BD decision | Made | Undecided — "add to agenda" | ❌ |
| Dogfooding 8hrs | Zero failures | 1 session (~1hr), found auth bug | ⚠️ |
| Blog post | Published | Not published | ❌ |

**Pattern:** Biggest time investment (README, ~60%) wasn't on the goal list. This is the perfectionism/scope-creep pattern called out in the limiting factor doc.
