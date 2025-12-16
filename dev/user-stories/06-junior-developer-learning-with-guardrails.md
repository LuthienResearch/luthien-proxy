# User Story 6: Junior Developer - Learning with Guardrails

## Persona

**Taylor** - A junior developer at an early-stage AI startup, working alongside a senior technical co-founder.

## Context

Taylor is a career-switcher learning to code via AI tools. They use Claude Code for most development work and can ship small features, but their debugging is unsystematic and they sometimes miss security issues or anti-patterns. Their senior co-founder (Morgan) wants to trust Taylor to work independently, but needs visibility into what the AI is doing and a way to catch mistakes before they hit production. The dynamic is trust-but-verify: Taylor should feel empowered, not micromanaged, while Morgan gets peace of mind.

## Story

> As Taylor, I want a complete log of what Claude did during my session—plus guardrails that catch common mistakes—so that I can learn from my AI-assisted work and my senior co-founder can review without hovering over my shoulder.

## Scenario

1. Taylor starts a Claude Code session to add a new API endpoint
2. Claude suggests creating a route, writing SQL, and adding tests
3. Taylor approves the changes, code gets written
4. Luthien logs every prompt, response, and tool call to `conversation_transcript`
5. Claude tries to hardcode an API key in the config file
6. Luthien's policy catches this: **injects a warning** "This looks like a hardcoded secret. Consider using environment variables."
7. Taylor sees the warning inline, fixes the approach
8. Later, Claude runs `rm -rf` on a directory Taylor didn't create
9. Luthien blocks it: "Blocked: destructive command on non-agent-created files. Ask Taylor to confirm."
10. End of session: Taylor shares the conversation log link with Morgan
11. Morgan reviews the session in 5 minutes, sees the two interventions, leaves a comment: "Good catch on the API key. Next time also check X."
12. Taylor learns from the feedback without needing a synchronous meeting

## Acceptance Criteria

- [ ] All prompts, responses, and tool calls logged with timestamps
- [ ] Conversation log exportable/viewable via URL
- [ ] Senior dev can review sessions asynchronously
- [ ] Guardrail policies catch: hardcoded secrets, destructive commands on user files, common anti-patterns
- [ ] Interventions appear inline (not as errors) so junior dev learns in-flow
- [ ] Senior dev can leave comments/annotations on session logs

## Required Features

### Core Infrastructure

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-5sr` | Conversation context tracking across requests | open | P1 |
| `luthien-proxy-fsb` | Message injection into response stream | open | P1 |

### UI Components

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-edl` | Conversation Viewer UI | open | P1 |
| TBD | Session sharing via URL | open | P2 |
| TBD | Comment/annotation on session logs | open | P2 |

### Policy Framework

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| TBD | Hardcoded secrets detection policy | open | P2 |
| TBD | Destructive command guardrails (context-aware) | open | P2 |
| `luthien-proxy-3yp` | Context-aware policy base class | open | P2 |

## Technical Touchpoints

- `storage/conversation_events`: Full conversation logging including tool calls
- `ui/conversation_viewer`: Session review interface
- `streaming/policy_executor`: Inline warning injection
- `policies/`: Guardrail policy implementations
- New: Session sharing and annotation system

## Implementation Status

**Overall Progress**: Not Started

### Phase 1: Logging Foundation
- [x] Conversation events stored with session linkage (done in conversation_transcript view)
- [ ] Include tool calls in conversation_transcript
- [ ] Exportable via URL

### Phase 2: Guardrail Policies
- [ ] Hardcoded secrets detection
- [ ] Destructive command detection (context-aware)
- [ ] Inline warning injection (not error)

### Phase 3: Review UI
- [ ] Session viewer with intervention highlights
- [ ] Shareable session URLs
- [ ] Comment/annotation system

## Dependencies

```
luthien-proxy-5sr (Conversation context tracking)
    └── luthien-proxy-edl (Conversation Viewer UI)
        └── Session sharing URLs
        └── Comment/annotation system

luthien-proxy-fsb (Message injection)
    └── Inline warning injection for guardrails
```

## Notes

- This persona represents the "trust-but-verify" dynamic common in small teams with mixed experience levels
- The guardrails should educate, not block unnecessarily—Taylor should feel empowered
- Async review is key: Morgan shouldn't need to pair-program to provide oversight
- Session logs become a learning artifact, not just an audit trail
