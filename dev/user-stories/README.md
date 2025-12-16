# User Stories

Canonical user stories that guide Luthien Control development. Each story represents a key persona and use case, with linked implementation issues and progress tracking.

## Stories

| # | Story | Persona | Status |
|---|-------|---------|--------|
| 1 | [Solo Developer: Context-Aware Safety](01-solo-developer-context-aware-safety.md) | Alex (Senior Developer) | Not Started |
| 2 | [Platform Team: Org-Wide Visibility](02-platform-team-org-visibility.md) | Jordan (Platform Engineer) | Not Started |
| 3 | [Researcher: Multi-Reviewer Evaluation](03-researcher-multi-reviewer-evaluation.md) | Sam (PhD Student) | Not Started |
| 4 | [Policy Author: Compliance with HITL](04-policy-author-compliance-hitl.md) | Riley (Security Engineer) | Not Started |
| 5 | [Infrastructure: Observability & Unification](05-infrastructure-observability-unification.md) | Core Developer | Not Started |
| 6 | [Junior Developer: Learning with Guardrails](06-junior-developer-learning-with-guardrails.md) | Taylor (Junior Developer) | Not Started |

## Feature Matrix

| Feature | Story 1 | Story 2 | Story 3 | Story 4 | Story 5 | Story 6 |
|---------|:-------:|:-------:|:-------:|:-------:|:-------:|:-------:|
| Conversation context tracking | **X** | **X** | | **X** | | **X** |
| Conversation Viewer UI | **X** | **X** | | | | **X** |
| Message injection | **X** | **X** | **X** | **X** | | **X** |
| Media attachment support | | **X** | | | | |
| Model param pass-through | **X** | | | | | |
| Parallel query (resampling) | | **X** | | | | |
| Per-user dashboards | | **X** | | | | |
| Escalation tiers | | **X** | | **X** | | |
| Multi-reviewer evaluation | | | **X** | | | |
| Rule extraction | | | **X** | | | |
| LLM rephrasing | | | **X** | **X** | | |
| Human-in-the-loop approval | | | | **X** | | |
| Approval UI | | | | **X** | | |
| Context-aware policy base | | | | **X** | | **X** |
| Compliance dashboard | | | | **X** | | |
| Unified endpoint processing | | | | | **X** | |
| Structured span hierarchy | | | | | **X** | |
| Session sharing URLs | | | | | | **X** |
| Session annotations/comments | | | | | | **X** |
| Guardrail policies (secrets, destructive cmds) | | | | | | **X** |

## Key Issues by Feature Area

### Core Infrastructure (P1)

| Issue | Title | Stories |
|-------|-------|---------|
| `luthien-proxy-5sr` | Conversation context tracking across requests | 1, 2, 4 |
| `luthien-proxy-fsb` | Message injection into response stream | 1, 2, 3, 4 |
| `luthien-proxy-edl` | Conversation Viewer UI | 1, 2 |
| `luthien-proxy-mfs` | thinking and verbosity model flags not respected | 1 |
| `luthien-proxy-en1` | Unify OpenAI and Anthropic endpoint processing | 5 |
| `luthien-proxy-a0r` | Structured span hierarchy for request processing | 5 |

### Dashboards & UI (P2)

| Issue | Title | Stories |
|-------|-------|---------|
| `luthien-proxy-ay5` | Per-user and per-conversation dashboards | 2 |
| `luthien-proxy-ap2` | Approval queue UI | 4 |
| `luthien-proxy-4yh` | Compliance audit dashboard | 4 |

### Policy Framework (P2)

| Issue | Title | Stories |
|-------|-------|---------|
| `luthien-proxy-3yp` | Context-aware policy base class | 1, 4 |
| `luthien-proxy-kjr` | Multi-reviewer parallel policy evaluation | 3 |
| `luthien-proxy-7ib` | Rule extraction from request context | 3 |
| `luthien-proxy-8gv` | LLM rephrasing of policy decisions | 3, 4 |
| `luthien-proxy-rtu` | Human-in-the-loop approval workflow | 4 |

### Platform Features (P2)

| Issue | Title | Stories |
|-------|-------|---------|
| `luthien-proxy-kxh` | Media attachment support | 2 |
| `luthien-proxy-822` | Parallel query (resampling) support | 2 |
| `luthien-proxy-aai` | Escalation tiers with webhook alerts | 2, 4 |

## Dependency Graph

```
                    ┌─────────────────────────────────┐
                    │  luthien-proxy-5sr              │
                    │  Conversation context tracking  │
                    └───────────┬─────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
            ▼                   ▼                   ▼
┌───────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ luthien-proxy-edl │ │luthien-proxy-3yp│ │luthien-proxy-ay5│
│ Conversation UI   │ │ Context-aware   │ │ Per-user        │
│                   │ │ policy base     │ │ dashboards      │
└───────────────────┘ └─────────────────┘ └─────────────────┘


┌─────────────────────────────────┐
│  luthien-proxy-7ib              │
│  Rule extraction                │
└───────────┬─────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  luthien-proxy-kjr              │
│  Multi-reviewer evaluation      │
└─────────────────────────────────┘


┌─────────────────────────────────┐
│  luthien-proxy-rtu              │
│  Human-in-the-loop approval     │
└───────────┬─────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  luthien-proxy-ap2              │
│  Approval queue UI              │
└─────────────────────────────────┘
```

## Implementation Priority

Based on feature dependencies and story coverage:

### Wave 0: Infrastructure (Do First)

1. `luthien-proxy-en1` - Unify OpenAI and Anthropic endpoint processing
2. `luthien-proxy-a0r` - Structured span hierarchy for request processing

### Wave 1: Foundation

1. `luthien-proxy-5sr` - Conversation context tracking (blocks 3 features)
2. `luthien-proxy-fsb` - Message injection (used by all 4 stories)
3. `luthien-proxy-mfs` - Model parameter pass-through (existing bug)

### Wave 2: UI & Visibility

1. `luthien-proxy-edl` - Conversation Viewer UI
2. `luthien-proxy-ay5` - Per-user dashboards
3. `luthien-proxy-aai` - Escalation tiers

### Wave 3: Advanced Policies

1. `luthien-proxy-3yp` - Context-aware policy base
2. `luthien-proxy-7ib` - Rule extraction
3. `luthien-proxy-kjr` - Multi-reviewer evaluation
4. `luthien-proxy-8gv` - LLM rephrasing

### Wave 4: Compliance & Approval

1. `luthien-proxy-rtu` - Human-in-the-loop approval
2. `luthien-proxy-ap2` - Approval queue UI
3. `luthien-proxy-4yh` - Compliance dashboard

### Wave 5: Platform Polish

1. `luthien-proxy-kxh` - Media attachment support
2. `luthien-proxy-822` - Parallel query support

## How to Use These Stories

1. **Planning**: Use the feature matrix to identify which stories benefit from a feature
2. **Prioritization**: Check the implementation priority for suggested build order
3. **Progress**: Update story status and checkboxes as features are completed
4. **Context**: Reference stories when implementing features to understand user intent
5. **Testing**: Use scenarios as acceptance test cases

## Updates

- **2025-12-10**: Initial creation with 4 canonical user stories
- **2025-12-10**: Added Story 5 (Infrastructure) with unified endpoint processing and structured span hierarchy
- **2025-12-16**: Added Story 6 (Junior Developer) for learning with guardrails - trust-but-verify persona
