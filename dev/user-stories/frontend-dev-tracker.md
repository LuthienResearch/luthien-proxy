# Front-end Development and User Feedback Tracker

**Purpose**: Track progress toward validated product-market fit using the [Learn More Faster](https://www.gv.com/research-sprint) methodology.

**Document owner**: Scott (front-end builder with BD instincts)

---

## Goals Overview

| Goal | Description | Key Metric |
|------|-------------|------------|
| **Goal 1** | Unblock dogfooding | Scott can use Luthien daily for real work |
| **Goal 2** | Unblock live external user trial | 1 external user completes a guided session |
| **Goal 3** | Unblock independent user trials | Users can try Luthien without hand-holding |

**Learn More Faster principle**: Each goal builds toward the next. We validate assumptions at each stage before investing in the next.

---

## Goal 1: Unblock Dogfooding

**Definition of done**: Scott can use Luthien daily for Claude Code sessions, with conversations logged and viewable.

### Current Blockers

| Blocker | Severity | Owner | Status |
|---------|----------|-------|--------|
| [List specific bugs blocking dogfooding] | P0 | Jai | Open |
| Session viewer needs polish | P1 | Scott | In Progress |

### Approach Options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Fix in main** | Jai fixes blocking bugs | Clean history, single source | Depends on Jai's bandwidth |
| **B: Fix in branch** | Scott fixes in feature branch | Unblocked immediately | Branch diverges from main |
| **C: Fork for dogfooding** | Clone repo, merge Scott's PRs | Full control | Maintenance burden, sync issues |

**Current decision**: [TBD - discuss with Jai]

### Milestones

- [ ] Identify and document all blocking bugs
- [ ] Get Luthien running locally with real Claude Code sessions
- [ ] View session in `/history` UI
- [ ] Complete one full dogfooding day

---

## Goal 2: External Guided Trial + Required Features

**Definition of done**: One external user can complete a guided 30-minute Luthien trial with Scott facilitating.

### User Journey (Guided Trial)

1. **Setup** (5 min): User has Luthien running locally
2. **First conversation** (10 min): User sends prompts through Luthien proxy
3. **View history** (5 min): User sees their conversation in `/history` UI
4. **Understand logging** (5 min): Overview of what's captured in the database
5. **[Future] Policy demo** (5 min): Show a simple policy in action

### Required Features (Priority Order)

#### Phase 1: Conversation Viewer Polish

| Feature | Status | PR |
|---------|--------|-----|
| Link from gateway homepage to `/history` | Done | #132 |
| Session timestamps (start/end) | Done | #133 |
| First user message as preview | Done | #133 |
| [TBD: Additional polish items] | - | - |

#### Phase 2: Database Overview UI

| Feature | Status | Notes |
|---------|--------|-------|
| Show what tables exist | Not started | Help users understand data model |
| Event counts by type | Not started | "You have 47 tool calls logged" |
| Simple query interface? | Not started | May be overkill for trials |

#### Phase 3: Policy Introduction

| Feature | Status | Notes |
|---------|--------|-------|
| Pre-configured demo policy | Not started | Show value without config complexity |
| Policy event highlighting | Partial | Already shows interventions in UI |

### Milestones

- [ ] Conversation viewer ready for demo
- [ ] Create trial script/checklist
- [ ] Recruit first external trial user
- [ ] Complete first guided trial
- [ ] Document learnings

---

## Goal 3: Unblock Independent User Trials

**Definition of done**: A developer can try Luthien independently with <10 minutes of setup and clear next steps.

### North Star

```bash
# User types this in terminal:
luthien

# Luthien starts, auto-configures, and prompts for API key
# User immediately starts using Claude Code through Luthien
```

### Required Changes

#### Radical Onboarding Simplification

| Current State | Target State |
|---------------|--------------|
| Clone repo, install deps, configure .env | Single command install |
| Read multiple docs to understand setup | Auto-configuration with sensible defaults |
| Manual Docker compose | Embedded database or cloud-hosted option |

#### Documentation Simplification

| Document | Current | Target |
|----------|---------|--------|
| Main README | Setup-heavy, multiple paths | "Getting Started" in 3 steps |
| luthienresearch.org | [Audit needed] | Mirror simplified README |

#### Telemetry for Learning

| Feature | Description | Privacy Consideration |
|---------|-------------|----------------------|
| Opt-in usage sharing | With user approval, share anonymized usage | Clear consent flow |
| Session metadata | Who, when, how long | No conversation content |
| Feature usage | Which UIs accessed | Help prioritize development |

**Learn More Faster note**: In early stages, most learning happens on calls with users. Telemetry becomes more valuable once we have enough users that we can't talk to all of them.

### Milestones

- [ ] Design simplified install flow
- [ ] Implement single-command setup
- [ ] Simplify README
- [ ] Update luthienresearch.org
- [ ] Add opt-in telemetry
- [ ] First independent user completes trial

---

## Scott's Prioritized Work Plan

Based on Story 6 (Taylor/Junior Dev persona) - this is Scott's story.

### Why Story 6?

Taylor's persona maps directly to Scott:
- Junior developer working alongside senior co-founder
- Uses Claude Code for most work
- Needs visibility into sessions for async review
- Learning from feedback is core to the workflow

### Immediate Work (Chunk by Chunk)

#### Chunk 1: Session Viewer Quick Wins ✅

**Scope**: Small UI improvements to existing `/history` page

- [x] Add link from gateway homepage to `/history` (PR #132)
- [x] Show start AND end times (not just duration) (PR #133)
- [x] Display first user message as session preview (PR #133)

#### Chunk 2: Session Sharing URLs (~2 sessions)

**Scope**: Generate shareable permalinks for sessions

Enables core Story 6 workflow: "Taylor shares session log link in PR description"

**Design decision needed**:
- Simple: UUID-based URL (`/history/session/{uuid}`)
- Better: Short codes (`/s/abc123`)

**Acceptance criteria**:
- [ ] Each session has a copyable share link
- [ ] Link works without authentication (or with simple token)
- [ ] Link shows read-only view of session

---

## FAQs

### On Goal Prioritization

1. **"Why not fix blocking bugs yourself?"**
   - Some bugs may require deep backend knowledge
   - Risk of introducing new issues without code familiarity
   - Jai's review time might exceed fix time anyway

2. **"Why focus on UI before policies?"**
   - Conversation viewer is the "proof Luthien is working" moment
   - Easier to demo: "Look, all your conversations are logged"
   - Policies require more explanation and setup

3. **"Is Goal 3 premature?"**
   - Yes, it's aspirational. Goals 1-2 will inform what "simple" actually means.
   - Including it now helps us make decisions that don't paint us into corners.

### On Technical Approach

4. **"Should we invest in better onboarding infra now?"**
   - Not yet. Manual onboarding with 1-3 users teaches us what matters.
   - Build infra after we've done it manually 5+ times.

5. **"What about authentication/multi-tenancy?"**
   - Out of scope for Goals 1-2 (single-user local setup)
   - May become relevant for Goal 3 if we host a shared instance

6. **"How does this relate to existing user stories?"**
   - Story 6 (Taylor) is the primary driver
   - Story 5 (Infrastructure) provides foundation
   - Stories 1-4 inform future direction but aren't blockers

---

## Prioritization Summary

| Priority | Item | Goal | Rationale |
|----------|------|------|-----------|
| P0 | Fix dogfooding blockers | 1 | Can't validate anything without using it |
| P1 | Conversation viewer polish | 1, 2 | First thing users see, proves value |
| P1 | Session sharing URLs | 2 | Enables async review workflow |
| P2 | Database overview UI | 2 | Nice-to-have for trials |
| P2 | Trial script/checklist | 2 | Needed before external trials |
| P3 | Single-command install | 3 | Requires more infra investment |
| P3 | Telemetry | 3 | Useful after we have users |

---

## Links

- [User Stories README](README.md)
- [Story 6: Junior Developer](06-junior-developer-learning-with-guardrails.md)
- [UX Exploration Notes](../../branches/ux-exploration) *(if exists)*

---

## Changelog

- **2026-01-17**: Initial creation, merged plan of attack document, structured around Learn More Faster methodology
