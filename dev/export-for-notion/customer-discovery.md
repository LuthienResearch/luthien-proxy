# Luthien Customer Discovery: The Story So Far
## March 2025 – January 2026

---

## The Phases

| Phase | Question | Status |
|-------|----------|--------|
| 1 | What won't change? | ✅ Done |
| 2 | Is this a real problem? | ✅ Done |
| 3 | **Can we build something useful for ourselves?** | 🔄 HERE |
| 4 | Can we build something useful for others? | ⏳ Blocked |
| 5 | Can we charge for it? | ⏳ Blocked |

---

## Phase 1: "What won't change?" (Mar–May 2025) ✅

Scott joined March 2025 (trial), then as co-founder May 2025. Before iterating fast, we locked in the mission via [Theory of Change v12](https://docs.google.com/document/d/...).

**Core thesis:** What would it take for Redwood's AI control agenda to actually reduce x-risk in practice?

**Key fact:** 5-13 year runway → optimize for AI safety impact, not revenue.

---

## Phase 2: "Is this a real problem?" (Jun–Aug 2025) ✅

9 discovery interviews. People started saying the same things.

| Date | Name | Role | Transcript |
|------|------|------|------------|
| Jun 25 | Nico | CTO, Veleiro | [Link](https://docs.google.com/document/d/1u_WdPExBOLmQV4oSRZ1EX07MXUj74qsmoOxOppqLpG0/edit) |
| Jul 1 | Saranya | Staff Engineer, IntentAI | — |
| Jul 14 | Anthony | Sr. Developer, PauseAI | [Link](https://docs.google.com/document/d/1LIxnEXIcjL1gU7IT1wUtqzZOo4Sn49Z-ENKbrBdTI_c/edit) |
| Jul 23 | Aleksandr | Developer Advocate | [Link](https://docs.google.com/document/d/1gXeeI23EVa41BYJ_JCFlmQPLtbEDmyQfym77SaJpwBs/edit) |
| Jul 30 | Nathan | AI Safety Researcher | [Link](https://docs.google.com/document/d/1CbIkTinKXVUXgDycRsG73t6UwifqL0w0dB7--S8lF4I/edit) |
| Jul 30 | Vipul & Smriti | Echovane | [Link](https://docs.google.com/document/d/1PWJDWj_2jnFPPhzQjEi6qD_6-e3FAlX3lcCeoW6016U/edit) |
| Aug 18 | Kirk | CTO, Graphlit | [Link](https://docs.google.com/document/d/1KKJu0YwZq3bXVvlOD9LooC0l0GB8h-G3-w5BtKHeWXM/edit) |

**Validated problems:** [User Frustrations Database](https://docs.google.com/spreadsheets/d/1Ob_mgKqYZphRvli37X6DJQ-TdYtgBlXb5Y9TFxmEyec/edit)

**Resolution:** Problem is real. Diminishing returns on more interviews.

---

## Phase 3: "Can we build something useful for ourselves?" (Sep 2025–present) 🔄

**Status:** In progress. Not yet useful enough for daily use.

### What happened

Three architecture cycles:
1. DIY (Jan–Aug 2025): 562 commits, proved feasibility
2. LiteLLM-everything (Aug–Oct): Delegated too much, lost control
3. Integrated (Oct 16+): [PR #40](https://github.com/LuthienResearch/luthien-proxy/pull/40), brought critical components back in-house

Demos forced shipping (EAG NYC, AI Tinkerers Oct 22-27), but product still too buggy for real dogfooding.

### Current state (Jan 2026)

- Too buggy for daily use
- Scope too broad — need to narrow using existing tools (Langfuse, OpenTelemetry)
- Scott shifted from "Claude coach me" to simpler: "just save my data"
- demo to Seldon folks on sat (Jan 24) hit 500 errors from thinking blocks streaming bug. [PR #134](https://github.com/LuthienResearch/luthien-proxy/pull/134) fixed it, but pattern persists — need demo prep checklist, not just code fixes.

Jai is also working on related side projects to solve related frustrations (run claude code remotely, on mobile etc.) that might be useful to Luthien too:
- **Clarvis**: Web UI for managing Claude Code sessions remotely (mobile)
- **CloudKeeper**: Wrapper around Anthropic Agents SDK for multi-session management
- **Pluribus**: Multiple agents working on parallel branches of same repo

**Jai's observation:** Models are getting noticeably better. His pdoom has gone down. This raises an open question: **does model improvement reduce the leverage of control work?** (See [Jan 20 call](https://docs.google.com/document/d/1qjjQXVsuoYo-_zCJ-lpm13h4MQ8PowbK3s-DrXne-ok/edit))

### Exit criteria

- [ ] Scott & Jai use it 2+ weeks without wanting to turn it off
- [ ] At least one policy catches something real (test deletion, context rot, etc.)
- [ ] Setup works on first try

---

## Phase 4: "Can we build something useful for others?" (Future) ⏳

Give it away free (open-source). Gather feedback. Iterate.

**Blocked on:** Phase 3 exit criteria

### Pipeline ready

| Type | Count |
|------|-------|
| Potential users (expressed interest) | 50 |
| Networking contacts | 20+ |
| Total tracked | 196 |

[Luthien People Database](https://docs.google.com/spreadsheets/d/1lKNWdLRrkRu4VtxLsdWnKF_OFPBXAgG1aMioS9shHSM/edit)

---

## Phase 5: "Can we charge for it?" (Future) ⏳

TBD business model. Freemium or other.

**Blocked on:** Phase 4 learnings

**Top uncertainty:** Can grants sustain a developer tools company long-term? (Current confidence: 60-70%)

---

## Source Documents

- [Theory of Change v12](https://docs.google.com/document/d/.../edit)
- [User Frustrations Database](https://docs.google.com/spreadsheets/d/1Ob_mgKqYZphRvli37X6DJQ-TdYtgBlXb5Y9TFxmEyec/edit)
- [Luthien People Database](https://docs.google.com/spreadsheets/d/1lKNWdLRrkRu4VtxLsdWnKF_OFPBXAgG1aMioS9shHSM/edit)
- [Josh/Scott Sync - Dec 16](https://docs.google.com/document/d/12fhPHYzGudADHVZhHz3OCakbOpPwL_9Hv0yj8mWissU/edit)

---

*Last updated: Sunday, January 25, 2026*
