# Luthien Shipping Log

**Time period**: Dec 1, 2025 → Jan 29, 2026

---

## User Stories

Luthien serves 6 canonical user stories. See [full details](https://github.com/LuthienResearch/luthien-proxy/blob/main/dev/user-stories/README.md).

| # | Story | Persona | Progress |
|---|-------|---------|----------|
| 1 | Solo Developer: Context-Aware Safety | Alex (Senior Dev) | ~40% |
| 2 | Platform Team: Org-Wide Visibility | Jordan (Platform Eng) | ~15% |
| 3 | Researcher: Multi-Reviewer Evaluation | Sam (PhD Student) | ~20% |
| 4 | Policy Author: Compliance with HITL | Riley (Security Eng) | 0% |
| 5 | Infrastructure: Observability & Unification | Core Developer | ~95% |
| 6 | Junior Developer: Learning with Guardrails | Taylor (Junior Dev) | ~15% |

---

## Implementation Waves

| Wave | Focus | Status |
|------|-------|--------|
| 0 | Infrastructure (enables all stories) | Complete |
| 1 | Foundation (context tracking, message injection) | In progress |
| 2 | UI & Visibility (conversation viewer, dashboards) | In progress |
| 3 | Advanced Policies | Future |
| 4 | Compliance & Approval | Future |
| 5 | Platform Polish | Future |

---

## Goals

We're using [Learn More Faster](https://www.gv.com/research-sprint): validate at each stage before investing in the next.

| Goal | Description | Done When | Status |
|------|-------------|-----------|--------|
| 1 | Unblock dogfooding | Scott & Jai use Luthien daily | 3/4 blockers fixed |
| 2 | Unblock guided external trials | 3 users complete 30-min session | In progress |
| 3 | Unblock independent trials | <10 min setup | Not started |

### Goal 1 Blockers

| Blocker | Date | Status | PR |
|---------|------|--------|-----|
| Thinking blocks (non-streaming) | Jan 20 | Done | [#131](https://github.com/LuthienResearch/luthien-proxy/pull/131) |
| Thinking blocks (streaming) | Jan 26 | Done | [#134](https://github.com/LuthienResearch/luthien-proxy/pull/134) |
| Session viewer polish | Jan 26 | Done | [#133](https://github.com/LuthienResearch/luthien-proxy/pull/133) |
| `context_management` param breaks Claude Code | Jan 29 | In progress | [#143](https://github.com/LuthienResearch/luthien-proxy/pull/143) (partial) |

### Goal 2 Progress

| Milestone | Date | Status | Notes |
|-----------|------|--------|-------|
| Railway deployment live | Jan 29 | Done | `luthien-proxy-production-0b7d.up.railway.app` |
| Demo checklist created | Jan 29 | Done | [luthien-org/demo-checklist.md](https://github.com/LuthienResearch/luthien-org/blob/main/demo-checklist.md) |
| Quick start guide | Jan 29 | Done | [luthien-org/demo-quick-start.md](https://github.com/LuthienResearch/luthien-org/blob/main/demo-quick-start.md) |
| Counterweight demo | Jan 29 | Scheduled | 3:30pm - using Codex (Claude Code has bug) |

---

## PRs Shipped

### December (Remote)

| Date | What | PR | Who | Cat |
|------|------|-----|-----|-----|
| Dec 4 | E2E tests using Claude Code | [#77](https://github.com/LuthienResearch/luthien-proxy/pull/77) | Jai | Infra |
| Dec 4 | API request tracing + debug UI | [#80](https://github.com/LuthienResearch/luthien-proxy/pull/80) | Jai | UX |
| Dec 4 | Codebase cleanup | [#81](https://github.com/LuthienResearch/luthien-proxy/pull/81) | Jai | Infra |
| Dec 8 | DI for EventEmitter | [#83](https://github.com/LuthienResearch/luthien-proxy/pull/83) | Jai | Infra |
| Dec 9 | Remove prisma dependency | [#84](https://github.com/LuthienResearch/luthien-proxy/pull/84) | Jai | Infra |
| Dec 9 | Auth for debug endpoints | [#86](https://github.com/LuthienResearch/luthien-proxy/pull/86) | Jai | Infra |
| Dec 9 | Centralize env config (pydantic) | [#87](https://github.com/LuthienResearch/luthien-proxy/pull/87) | Jai | Infra |
| Dec 9 | Session-based login for admin UIs | [#88](https://github.com/LuthienResearch/luthien-proxy/pull/88) | Jai | UX |
| Dec 9 | Centralize magic numbers | [#89](https://github.com/LuthienResearch/luthien-proxy/pull/89) | Jai | Infra |
| Dec 10 | Simplify policy storage | [#90](https://github.com/LuthienResearch/luthien-proxy/pull/90) | Jai | Infra |
| Dec 11 | Fix migration script | [#91](https://github.com/LuthienResearch/luthien-proxy/pull/91) | Scott | Bug |
| Dec 11 | Unify OpenAI/Anthropic endpoints | [#92](https://github.com/LuthienResearch/luthien-proxy/pull/92) | Jai | Infra |
| Dec 11 | Named constants | [#93](https://github.com/LuthienResearch/luthien-proxy/pull/93) | Scott | Infra |
| Dec 11 | README improvements | [#96](https://github.com/LuthienResearch/luthien-proxy/pull/96), [#99](https://github.com/LuthienResearch/luthien-proxy/pull/99), [#100](https://github.com/LuthienResearch/luthien-proxy/pull/100) | Scott | Docs |
| Dec 13 | Session ID tracking | [#102](https://github.com/LuthienResearch/luthien-proxy/pull/102) | Jai | Infra |
| Dec 15 | DI for create_app | [#105](https://github.com/LuthienResearch/luthien-proxy/pull/105) | Jai | Infra |
| Dec 16 | Login page UX | [#106](https://github.com/LuthienResearch/luthien-proxy/pull/106) | Scott | UX |
| Dec 16 | Structured span hierarchy | [#107](https://github.com/LuthienResearch/luthien-proxy/pull/107) | Jai | Infra |
| Dec 16 | Migration validation | [#110](https://github.com/LuthienResearch/luthien-proxy/pull/110) | Jai | Infra |
| Dec 16 | Production readiness | [#101](https://github.com/LuthienResearch/luthien-proxy/pull/101) | Scott | Infra |
| Dec 17 | Unit test coverage 84%→90% | [#115](https://github.com/LuthienResearch/luthien-proxy/pull/115) | Jai | Infra |
| Dec 18 | Fix proxy with images | [#104](https://github.com/LuthienResearch/luthien-proxy/pull/104) | Scott | Bug |
| Dec 18 | Reorganize LLM types | [#117](https://github.com/LuthienResearch/luthien-proxy/pull/117) | Jai | Infra |
| Dec 22 | Conversation History Viewer | [#119](https://github.com/LuthienResearch/luthien-proxy/pull/119) | Jai | UX |
| Dec 22 | ParallelRulesPolicy | [#120](https://github.com/LuthienResearch/luthien-proxy/pull/120) | Jai | Infra |
| Dec 22 | Public demo deployment | [#121](https://github.com/LuthienResearch/luthien-proxy/pull/121), [#122](https://github.com/LuthienResearch/luthien-proxy/pull/122) | Jai | Infra |
| Dec 29 | Policy Config auto-discovery | [#123](https://github.com/LuthienResearch/luthien-proxy/pull/123) | Jai | UX |

### January (Seattle / Mox)

| Date | What | PR | Who | Cat |
|------|------|-----|-----|-----|
| Jan 9 | Extra model params passthrough | [#126](https://github.com/LuthienResearch/luthien-proxy/pull/126) | Jai | Bug |
| Jan 9 | Policy Config UI improvements | [#125](https://github.com/LuthienResearch/luthien-proxy/pull/125) | Jai | UX |
| Jan 17 | Convo history improvements | [#133](https://github.com/LuthienResearch/luthien-proxy/pull/133) | Scott | UX |
| Jan 20 | Thinking blocks (non-streaming) | [#131](https://github.com/LuthienResearch/luthien-proxy/pull/131) | Scott | Bug |
| Jan 20 | Gateway homepage fixes | [#132](https://github.com/LuthienResearch/luthien-proxy/pull/132) | Scott | UX |
| Jan 23 | Story 6 docs | [#114](https://github.com/LuthienResearch/luthien-proxy/pull/114) | Scott | Docs |
| Jan 23 | TODO updates | [#130](https://github.com/LuthienResearch/luthien-proxy/pull/130) | Scott | Docs |
| Jan 24 | Thinking blocks (streaming) | [#134](https://github.com/LuthienResearch/luthien-proxy/pull/134) | Scott | Bug |
| Jan 24 | RFC: AGENTS.md workflow | [#135](https://github.com/LuthienResearch/luthien-proxy/pull/135) | Scott | Docs |
| Jan 26 | Refactor thinking_blocks field | [#138](https://github.com/LuthienResearch/luthien-proxy/pull/138) | Jai | Infra |
| Jan 29 | Stricter typing in history service | [#139](https://github.com/LuthienResearch/luthien-proxy/pull/139) | Jai | Infra |
| Jan 29 | Fix LiteLLM update issues | [#143](https://github.com/LuthienResearch/luthien-proxy/pull/143) | Jai | Bug |
| Jan 29 | Forward backend API errors | [#146](https://github.com/LuthienResearch/luthien-proxy/pull/146) | Jai | Bug |
| Jan 29 | PR hygiene guideline | [#149](https://github.com/LuthienResearch/luthien-proxy/pull/149) | Scott | Docs |
| Jan 29 | StringReplacementPolicy | [#150](https://github.com/LuthienResearch/luthien-proxy/pull/150) | Jai | Feature |

---

## Contributors

| Who | Merged | Open | Focus |
|-----|--------|------|-------|
| Jai | 31 | 0 | Infrastructure, foundation, policy framework |
| Scott | 14 | 3 | Bugs, UX polish, docs |

---

## Related

- [User Stories README](https://github.com/LuthienResearch/luthien-proxy/blob/main/dev/user-stories/README.md)
- [UI Feedback Tracker](UI-feedback-dev-tracker.md)
- [Demo Checklist](https://github.com/LuthienResearch/luthien-org/blob/main/demo-checklist.md)
- [Demo Quick Start](https://github.com/LuthienResearch/luthien-org/blob/main/demo-quick-start.md)

*Updated: 2026-01-29*
