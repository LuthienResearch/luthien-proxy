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

## Claude Desktop Prompt (for vendor research)

See below - copy this into Claude Desktop for the research portion.
