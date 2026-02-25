# Weekly Check-in

**Date:** Feb 26

**Company:** Luthien

---

## What did you ship last week?

1. 🔥🎉 **Jai: Closed out the auth chain (continuation of last week's massive infra push)** — 8 PRs on Thursday Feb 19 alone (#211-#217, #219), plus final OAuth passthrough (#221) over the weekend. Then sick Monday. Combined with last week's composable policies, config UI, and live view — Claude Code relay now works end-to-end. 🚀 The #1 technical blocker from Feb 19 is resolved.
2. 🧹✨ **Scott: README/landing page overhaul incorporating Tyler + Jai feedback** — PR #179 (56 commits). Before/After SVG diagrams showing real Claude Code UX, simplified Quick Start, question-based headers. Extracted Tyler's specific feedback from Feb 10 live install. Tyler reviewing async, call next week. Feeds QA hiring pipeline (Trello: "update readme → hire upwork people" ✅ Done).
3. **Scott: BD + dogfooding + prep work** — Tyler follow-up (reviewing landing page async, call next week), dogfooding found auth bug (PR #222 with RCA/COE), QA hiring pipeline moving (instructions + Upwork draft), BD (LiteLLM, Govind, PBC lawyer), started policy brainstorming, plus 5 housekeeping PRs.

---

## What's blocking you right now?

**The blocker:** No useful policy exists. We have infrastructure for composable policies, auth, config UI, live view — but `policy_config.yaml` still defaults to NoOpPolicy. We can't dogfood meaningfully, demo value to leads, or convert anyone to a trial without a policy that solves a real pain point.

**Who can help?** Jai + Scott need to pick one policy and build it this week. Finn/Esben: help us decide which one (see [Limiting Factor doc](TBD_HACKMD_LINK)).

---

## What are your goals for next week?

### Scott's goals:

1. **Get one useful policy running end-to-end.** Binary: policy is loaded in config, tested, and used in 5+ coding sessions by Friday Mar 6.
2. **Tyler call + landing page review.** Binary: call happens, Tyler gives feedback on async review.
3. **Book 5 EAG follow-up calls.** Binary: 5 calls on calendar by Friday Mar 6.

### Jai's goals:

1. _(for Jai to fill in)_

---

## 📊 Batch North Star Metric

**Metric:** 3 paying customers by Demo Day (April 2026)

**Current status:** 0 paying. ~15 EAG leads in pipeline (aging but Tyler actively engaged). Auth blockers resolved. Infrastructure ready (composable policies, config UI, live view, Railway deploy). But no useful policy to show anyone yet.

**On track?** ❌ — Pipeline exists but isn't being converted. The bottleneck isn't leads or infrastructure; it's that we have nothing useful to put in front of people yet. See [Limiting Factor analysis](TBD_HACKMD_LINK) for full breakdown.

---

## Scorecard: Last Week's Goals

| Goal | Target | Result | |
|------|--------|--------|--|
| EAG SF follow-ups | 5+ meetings by Feb 27 | Some follow-up actions (Diogo, Marius/Jai meeting), unclear how many calls booked | ⚠️ |
| Hire Upwork QA | Hired | Instructions page + Upwork draft ready, no hire yet | ⚠️ |
| Tyler deployment | 2nd user in logs | Tyler engaged — reviewing landing page async, call next week. Team not using yet | ⚠️ |
| Yoeri BD decision | Made | Undecided — "add to agenda" | ❌ |
| Dogfooding 8hrs | Zero failures | 1 session (~1hr), found auth bug → PR #222 | ⚠️ |
| Blog post | Published | Not published | ❌ |
