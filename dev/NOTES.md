# Outsourcing Analysis: QA + Bug Fixing

**Date:** 2026-02-02

---

## Bug Analysis: October 2025 - February 2026

**Stats:** 34 bug-fix PRs, ~50 fix commits, 5 open bug PRs right now

### Category 1: Client Compatibility (QA: YES ✅, Outsource Fix: YES ✅)
*Highest impact for customer experience*

| PR | Bug |
|----|-----|
| #167 | Orphaned tool_results after /compact |
| #166 | Codex tool-call sequencing error |
| #162 | Codex developer role not accepted |
| #161 | /compact causes duplicate tool names |
| #151 | context_management param breaks Anthropic |

**Pattern:** Claude Code/Codex updates break things. Regression bugs.
**QA catchability:** HIGH - manual testing catches immediately
**Outsource fix:** YES - well-scoped, clear repro steps

### Category 2: Streaming/SSE Protocol (QA: PARTIAL ⚠️, Outsource Fix: MAYBE)

| PR | Bug |
|----|-----|
| #134, #131 | Thinking blocks stripped |
| #76 | Multi-tool-call streaming bugs |
| #75 | message_delta missing for Anthropic |
| (unreleased) | StringReplacementPolicy drops finish_reason |

**Pattern:** SSE protocol is tricky - exact event order/structure matters
**QA catchability:** MEDIUM - notice "response cut off" but not why
**Outsource fix:** HARDER - requires understanding streaming architecture

### Category 3: Format Conversion (QA: NO ❌, Outsource Fix: NO ❌)

| PR | Bug |
|----|-----|
| #104 | Images crash proxy |
| (changelog) | tool_choice format broken |

**Pattern:** OpenAI ↔ Anthropic edge cases
**QA catchability:** LOW - needs specific test scenarios
**Outsource fix:** NO - requires deep protocol knowledge

### Category 4: LiteLLM Dependency (QA: YES ✅, Outsource Fix: MAYBE)

| PR | Bug |
|----|-----|
| #143 | litellm update breaks things |
| #155 | litellm type import breaks Docker |

**QA catchability:** HIGH - test after pip upgrade
**Outsource fix:** DEPENDS - sometimes trivial, sometimes deep

### Category 5: UI/UX (QA: YES ✅, Outsource Fix: YES ✅)

| PR | Bug |
|----|-----|
| #132 | Gateway homepage broken |
| #73 | Activity monitor broken |

**QA catchability:** HIGH - just click through
**Outsource fix:** YES - straightforward frontend fixes

### Category 6: Deployment/Infra (QA: MAYBE, Outsource Fix: NO ❌)

| PR | Bug |
|----|-----|
| #122 | Demo deployment broken |
| #91 | Migration script broken |

**Outsource fix:** NO - requires infra context

### Category 7: Policy Implementation (QA: NO ❌, Outsource Fix: NO ❌)

| PR | Bug |
|----|-----|
| #147 | SimplePolicy non-streaming broken |
| #71 | ToolCallJudgePolicy inheritance wrong |

**Pattern:** Internal API correctness - unit tests catch these
**Outsource fix:** NO - core architecture

---

## What's Outsourceable?

### HIGH confidence (do it)
- **QA:** Client compatibility, UI, basic flows, post-upgrade testing
- **Bug fixing:** Client compat bugs, UI bugs (clear repro, scoped changes)

### MEDIUM confidence (case by case)
- **QA:** Streaming issues (canary detection)
- **Bug fixing:** LiteLLM compat (sometimes trivial)

### LOW confidence (keep in-house)
- **Bug fixing:** Streaming architecture, format conversion, policy internals

---

## Cost-Benefit Quick Math

**Current state costs:**
- 2-4 hrs/bug × ~10 bugs/month = 20-40 eng hours/month
- Reputation risk before demo day
- Demo failure risk

**Outsourced QA:** $2-4K/month
**Outsourced bug fixing:** $50-150/hr, maybe 10-20 hrs/month = $500-3K/month

**ROI:** If catches/fixes 5 bugs that would take 3 hrs each = 15 hrs saved
At $100-150/hr eng cost = $1,500-2,250 value + intangibles

---

## Model Options

### For QA
1. **Part-time contractor** ($2-4K/mo) - learns product, available on-demand
2. **Crowdsourced (Rainforest, Testlio)** ($1-3K/mo) - diverse coverage, scalable
3. **Offshore team** ($1.5-3K/mo) - lower rates, time zone coverage

### For Bug Fixing
1. **Claude Code/Codex agents** - FREE, already have, good for scoped bugs
2. **Contractor (Upwork/Toptal)** - $50-150/hr, good for multi-file fixes
3. **Hybrid** - Agents find/propose fix, contractor validates/ships

---

## Recommendation

**Phase 1 (Now → Demo Day):**
1. Hire part-time QA contractor ($2-4K/mo) for Claude Code/Codex regression testing
2. Use Claude Code agents for bug fixing (you already do this)
3. Escalate to contractor only for bugs agents can't handle

**Phase 2 (Post Demo Day):**
Evaluate data - did QA catch real bugs? Scale accordingly.

---

---

## Human-Only vs Automatable Testing

### Genuinely Requires Humans (Not Fixable)

| What | Why |
|------|-----|
| **Real Claude Code through proxy** | Agent can't run itself as a client and observe |
| **Real Codex through proxy** | Same self-reference problem |
| **Client behavior drift** | Claude Code/Codex ship updates with new wire formats - mocks based on yesterday's behavior won't catch tomorrow's bugs |

**This is the core argument for outsourced QA** - you need a human running the real clients.

### Automatable But Not Yet Done

| What | Fix | Effort |
|------|-----|--------|
| UI visual testing | Playwright | Medium - Jai may already have this |
| Traffic recording → replay tests | Record real sessions, replay as fixtures | Medium |
| Expand e2e with client message fixtures | Capture real Claude Code/Codex payloads | Low-Medium |
| Post-/compact behavior | Add test fixtures for compacted conversations | Low |

**Key insight:** Even with automation, you still need humans to CAPTURE the real client behavior that becomes test fixtures. Automation replays known-good behavior; humans discover new edge cases.

### The Dogfooding Bottleneck

Scott's goal: Dogfood Luthien → Build better UX features

Current blocker: Bugs from client compatibility keep interrupting

**Automation reduces future interruptions** but requires upfront investment. QA contractor catches bugs NOW while automation gets built.

---

---

## Draft Slack to Jai

```
Hey - wanted to flag something I've been thinking about.

**The problem:** This weekend I hit several Codex bugs, and today found another Claude Code bug:
- PR #162: Codex developer role not accepted
- PR #164: Codex chat wire API rendering bug
- PR #166: Codex tool-call sequencing error
- PR #167: Claude Code orphaned tool_results after /compact (today)

I know you're thinking about stripping out Codex/OpenAI support to simplify to just Claude bits, but I'm still worried these client compat bugs are going to keep slowing us down from dogfooding.

Every time Claude Code or Codex ships an update, something breaks. And I can't test this stuff myself because... Claude Code can't test Claude Code through the proxy (self-reference problem). Same with Codex.

**An idea:** What if we hired a part-time QA contractor ($2-4K/mo) to do manual regression testing before demo day? Their job: run real Claude Code sessions through the proxy after each release, catch the "it just broke" stuff before I hit it while trying to dogfood.

I did some analysis - we've had 34 bug-fix PRs since October, and about half are client compat issues that a human tester would catch immediately.

**Full analysis:** [Google Doc link - TODO: copy NOTES.md content here]

This is in addition to the pre-demo mechanisms and checklist we discussed: [pre-demo checklist link - TODO]

**Questions:**
1. Any concerns with this approach? Or does simplifying to Claude-only solve this enough?
2. Do you already have Playwright set up? (want to automate UI testing too)
3. Want me to handle vendor research/hiring or want to be involved?
```

---

## Claude Desktop Prompt (for vendor research)

Copy this into Claude Desktop:

```
I'm evaluating QA outsourcing options for a developer tools startup (AI proxy for Claude Code/Codex). Demo day is April 2026 with potential $3-7M funding. Budget: $2-5K/month for QA.

**What I need tested:**
- Claude Code compatibility (VS Code extension that sends requests through our proxy)
- Codex compatibility (OpenAI's coding agent)
- Basic API flows (send message → get response)
- UI pages (login, activity monitor, debug views)
- Post-dependency-upgrade regression testing

**Please research and compare:**

1. **Crowdsourced QA platforms** (Rainforest QA, Testlio, uTest, Applause)
   - Pricing models
   - Turnaround time
   - Quality of testers for developer tools
   - Minimum commitments

2. **QA contractor marketplaces** (Upwork, Toptal, Arc.dev)
   - Typical rates for QA engineers with API testing experience
   - How to filter for dev tools / CLI experience
   - Trial project ideas to evaluate candidates

3. **Offshore QA agencies**
   - Reputable agencies for startups
   - Typical engagement models
   - Communication/timezone considerations

4. **AI-assisted QA tools** that could supplement human QA
   - Are there tools that can automate Claude Code/Codex testing?
   - What about API contract testing automation?

For each option, give me:
- Estimated monthly cost
- Pros/cons for a 2-person startup
- How quickly I could get started
- Red flags to watch for

My main goal is catching regressions before users hit them, especially around our two main clients (Claude Code and Codex).
```
