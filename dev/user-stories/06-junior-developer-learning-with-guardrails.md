# User Story 6: Junior Developer - Learning with Guardrails

## Persona

**Taylor** - A junior developer at an early-stage AI startup, working alongside a senior technical co-founder. Previously a technical product manager at a FAANG company who is familiar with building and launching technical products, but doesn't have much coding experience.

**Focus area**: Front-end development - building onboarding tools, UI features, and user experience improvements. Works primarily with HTML/CSS/vanilla JS (no frameworks), with some Python for backend integration. Goal is to build small UI features autonomously so the senior dev can focus on core infrastructure.

## Context

Taylor uses Claude Code for most development work and can ship small features, but their debugging is unsystematic and they sometimes miss security issues or anti-patterns. Their senior co-founder (Morgan) needs visibility into what Taylor and her AI coding assistants/agents are doing to catch mistakes before they hit prod. The dynamic is trust-but-verify: Taylor should feel empowered to work independently on branches, not micromanaged, while Morgan gets peace of mind through async review at merge time.

**Pain point**: Taylor is very interested in process improvements and learning from feedback, but Claude Code's `/compact` feature discards conversation history. When Morgan leaves async feedback on a PR, Taylor often can't do a proper retrospective because the detailed session logs are gone. Luthien's persistent conversation logging solves this.

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
9. Luthien **warns** (not blocks): "This affects files not created in this session. Are you sure?"
10. Taylor acknowledges and continues (she has triage access - can push to branches)
11. End of session: Taylor pushes to her feature branch and opens a PR
12. Taylor shares the conversation log link in the PR description
13. Morgan reviews the session log before approving the merge to main
14. Morgan sees the `rm -rf` warning, leaves a comment: "Good that you caught the secrets issue. For the rm -rf, let's add a test to verify we're not deleting user data."
15. Taylor learns from the feedback, updates the PR, Morgan approves

**Key insight**: Guardrails educate in-the-moment; approval happens at merge time. Taylor is empowered to work independently on branches.

## Acceptance Criteria

- [ ] All prompts, responses, and tool calls logged with timestamps
- [ ] Conversation log exportable/viewable via URL (shareable in PR descriptions)
- [ ] Senior dev can review sessions asynchronously before merge approval
- [ ] Guardrail policies catch: hardcoded secrets, destructive commands on non-agent-created files
- [ ] Interventions are **warnings** (not blocks) - junior dev can acknowledge and continue
- [ ] Warnings appear inline in Claude's response (not as errors)
- [ ] Escalation with heads-up: if proceeding after warning, help draft Slack message to senior dev
- [ ] Media attachment support (screenshots of UI work)
- [ ] Session log highlights interventions for easy review
- [ ] Senior dev can leave comments/annotations on session logs

## Required Features

### Core Infrastructure

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-5sr` | Conversation context tracking across requests | open | P1 |
| `luthien-proxy-fsb` | Message injection into response stream | open | P1 |
| [PR #112](https://github.com/LuthienResearch/luthien-proxy/pull/112) | `conversation_transcript` view for human-readable logs | pushed | P1 |
| [PR #104](https://github.com/LuthienResearch/luthien-proxy/pull/104) | Media attachment support (images in conversations) | pushed | P2 |

> **Note**: Update status to "merged" when PRs are merged.

**Use case for conversation logging**: Senior dev (Morgan) can't always repro issues Taylor encounters. Taylor needs to share prompt/response logs plus debugging artifacts. The `conversation_transcript` view ([PR #112](https://github.com/LuthienResearch/luthien-proxy/pull/112)) enables this by extracting clean text from raw JSON payloads.

### UI Components

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-edl` | Conversation Viewer UI | open | P1 |
| TBD | Session sharing via URL | open | P2 |
| TBD | Comment/annotation on session logs | open | P2 |

**Current state (poor man's version)**: Export to CSV via `conversation_transcript` view, manually edit CSV to add comments, share file. Works but clunky.

**Future state**: Dedicated UI with permalink URLs for session sharing. See [TODO: Create visual database schema documentation](https://github.com/LuthienResearch/luthien-proxy/blob/main/dev/TODO.md) for related work.

**Open question**: Does GitHub already provide similar functionality via PR comments + linked artifacts? May not need custom UI if GitHub workflow is sufficient.

### Policy Framework

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| TBD | Hardcoded secrets detection policy | open | P2 |
| TBD | Destructive command guardrails (context-aware) | open | P2 |
| `luthien-proxy-3yp` | Context-aware policy base class | open | P2 |

**Policy ideas from dogfooding** (see `ux-exploration` branch):

1. **Commit Health Monitor** - Track files changed since last commit, alert if 10+ files with no commit. Encourages "commit small, commit often" habit.

2. **Scope Creep Detector** - Compare original request vs actual changes. Flag "Would you also like me to..." patterns. Alert when request was "fix login" but 5 features were added.

3. **Session Pattern Analyzer** - Cross-session insights: "You've worked on auth 3 days in a row - consider pairing." Weekly summary metrics.

4. **Learning Journal Generator** - Auto-document what was worked on each session. Generate weekly summaries for retros with senior dev.

**Key insight**: Best policies leverage **cross-session data** that Claude Code hooks can't see.

## Technical Touchpoints

- `storage/conversation_events`: Full conversation logging including tool calls
- `ui/conversation_viewer`: Session review interface
- `streaming/policy_executor`: Inline warning injection
- `policies/`: Guardrail policy implementations
- New: Session sharing and annotation system

## Implementation Status

**Overall Progress**: Started (Phase 1)

### Phase 1: Logging Foundation *(In Progress)*
- [x] Conversation events stored with session linkage
- [ ] `conversation_transcript` view for human-readable logs ([PR #112](https://github.com/LuthienResearch/luthien-proxy/pull/112) - pushed, awaiting review)
- [x] CSV export workflow documented (poor man's version)
- [ ] Include tool calls in conversation_transcript
- [ ] Permalink URLs for session sharing
- [ ] Media attachment support ([PR #104](https://github.com/LuthienResearch/luthien-proxy/pull/104) - awaiting fix)

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
- **Git-based approval model**: Taylor has triage access (can push branches), Morgan approves merges to main
- **Context-aware detection**: To know what's "agent-created", Luthien must track file creation events during the session (e.g., from tool calls like `Write` or `Bash(touch/mkdir)`). This requires persisting session state and comparing against it when destructive commands are issued.
