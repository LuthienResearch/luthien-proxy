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
4. Luthien logs every prompt, response, and tool call (viewable at `/history`)
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
- [ ] Conversation log exportable/viewable via URL (shareable via slack or in PR or Issue descriptions)
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
| [PR #112](https://github.com/LuthienResearch/luthien-proxy/pull/112) | `conversation_transcript` view for human-readable logs | closed | P1 |
| [PR #104](https://github.com/LuthienResearch/luthien-proxy/pull/104) | Media attachment support (images in conversations) | merged | P2 |

> **Note**: Update status to "merged" when PRs are merged.

**Use case for conversation logging**: Senior dev (Morgan) can't always repro issues Taylor encounters. Taylor needs to share prompt/response logs plus debugging artifacts. The `conversation_transcript` view ([PR #112](https://github.com/LuthienResearch/luthien-proxy/pull/112)) enables this by extracting clean text from raw JSON payloads.

### UI Components

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| [PR #119](https://github.com/LuthienResearch/luthien-proxy/pull/119) | Conversation Viewer UI | merged | P1 |
| TBD | Session sharing via URL | open | P2 |
| TBD | Comment/annotation on session logs | open | P2 |

**Current state**: Conversation History Viewer at `/history` ([PR #119](https://github.com/LuthienResearch/luthien-proxy/pull/119) merged). Basic functionality working.

**Improvements needed (2026-01-15 dogfooding feedback):**
- [x] Link from gateway homepage (PR #132)
- [ ] Rename session IDs to human-readable names (see design notes below)
- [ ] Show start time and end time (PR #133)

**Session naming design (2026-01-15):**

Research: Claude Code's `/resume` shows either the **initial prompt** (first message) or a **manual `/rename`** value. However, Claude Code also auto-generates titles using the prompt: *"Please write a 5-10 word title for the following conversation:"* — so there IS some auto-summarization happening.

| Claude Code shows | Luthien equivalent | Notes |
|-------------------|-------------------|-------|
| Initial prompt | `Start_session_description` | What user intended to work on |
| Manual `/rename` | Manual rename in Luthien UI? | Future feature |
| *(nothing)* | `End_session_description` | **Luthien value-add**: what actually happened |

**Recommendation:** Show `End_session_description` as the headline (unique value — Claude Code doesn't have this). Also display `Start_session_description` so users can cross-reference with Claude Code's `/resume` picker.

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
- [x] `conversation_transcript` view for human-readable logs ([PR #112](https://github.com/LuthienResearch/luthien-proxy/pull/112) - closed)
- [x] Include tool calls in conversation_transcript
- [ ] Permalink URLs for session sharing
- [x] Media attachment support ([PR #104](https://github.com/LuthienResearch/luthien-proxy/pull/104) - merged)

### Phase 2: Guardrail Policies
- [ ] Hardcoded secrets detection
- [ ] Destructive command detection (context-aware)
- [ ] Inline warning injection (not error)

### Phase 3: Review UI
- [x] Basic session viewer ([PR #119](https://github.com/LuthienResearch/luthien-proxy/pull/119) - merged)
- [ ] Session viewer improvements (human-readable names, start/end times, gateway link)
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

## Workflow Diagram

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Taylor        │     │    Luthien      │     │    Morgan       │
│  (Claude Code)  │     │    (Proxy)      │     │  (Senior Dev)   │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │  1. Prompt/Response   │                       │
         │──────────────────────>│                       │
         │                       │                       │
         │  2. Logs to /history  │                       │
         │     (conversation     │                       │
         │     viewer)           │                       │
         │                       │                       │
         │  3. Warning injected  │                       │
         │<──────────────────────│                       │
         │  "Hardcoded secret    │                       │
         │   detected"           │                       │
         │                       │                       │
         │  4. Taylor fixes,     │                       │
         │     pushes branch     │                       │
         │──────────────────────────────────────────────>│
         │                       │                       │
         │                       │  5. Morgan reviews    │
         │                       │     session log URL   │
         │                       │<──────────────────────│
         │                       │                       │
         │  6. Feedback on PR    │                       │
         │<──────────────────────────────────────────────│
         │                       │                       │
         │  7. Taylor does retro │                       │
         │     with full logs    │                       │
         │                       │                       │
```

## Example Policy Config

```yaml
# config/policy_config.yaml - Taylor's guardrails
policy:
  class: "luthien_proxy.policies.guardrails:GuardrailPolicy"
  config:
    # Warn on common mistakes, don't block
    mode: "warn"

    # Secrets detection
    secrets:
      enabled: true
      patterns:
        - "API_KEY\\s*=\\s*['\"][^'\"]+['\"]"
        - "password\\s*=\\s*['\"][^'\"]+['\"]"
        - "sk-[a-zA-Z0-9]{48}"
      message: "This looks like a hardcoded secret. Consider using environment variables."

    # Destructive command detection
    destructive_commands:
      enabled: true
      context_aware: true  # Only warn if target wasn't created this session
      patterns:
        - "rm -rf"
        - "DROP TABLE"
        - "DELETE FROM .* WHERE 1=1"
      message: "This affects files/data not created in this session. Are you sure?"

    # Escalation (heads-up to Morgan)
    escalation:
      enabled: true
      webhook_url: "${SLACK_WEBHOOK_URL}"
      template: |
        Taylor is proceeding with a flagged action:
        - Warning: {warning_message}
        - Session: {session_url}
        - Time: {timestamp}
```

## Example Policy Code

```python
# Pseudocode for Commit Health Monitor policy
class CommitHealthMonitorPolicy(EventBasedPolicy):
    """
    Tracks files changed since last commit.
    Warns if too many uncommitted changes accumulate.
    """

    THRESHOLD_FILES = 10
    THRESHOLD_LINES = 500

    async def on_tool_call(self, context: PolicyContext) -> PolicyDecision:
        tool_name = context.tool_call.get("name")

        # Track file modifications
        if tool_name in ["Write", "Edit"]:
            file_path = context.tool_call.get("file_path")
            await self.track_modified_file(context.session_id, file_path)

        # Check git status periodically
        if tool_name == "Bash" and "git" not in context.tool_call.get("command", ""):
            stats = await self.get_uncommitted_stats(context.session_id)

            if stats["files"] > self.THRESHOLD_FILES:
                return PolicyDecision(
                    action="warn",
                    message=f"You have {stats['files']} uncommitted files. "
                           f"Consider committing - small commits are easier to review!"
                )

        return PolicyDecision(action="allow")
```

## Onboarding Checklist

### For Taylor (Junior Dev)
- [ ] Luthien proxy running locally (`./scripts/quick_start.sh`)
- [ ] Claude Code configured to use proxy (`ANTHROPIC_BASE_URL=http://localhost:8000`)
- [ ] Understand warning vs blocking (warnings educate, you can proceed)
- [ ] Know how to export session logs for PR descriptions
- [ ] Bookmark the conversation viewer URL

### For Morgan (Senior Dev)
- [ ] Slack webhook configured for escalation notifications
- [ ] Familiar with `/history` UI for session review
- [ ] Review workflow: check session log → leave PR comment → approve/request changes
- [ ] Set expectations with Taylor: "I'll review logs async, ping me if urgent"

## Notes

- This persona represents the "trust-but-verify" dynamic common in small teams with mixed experience levels
- The guardrails should educate, not block unnecessarily—Taylor should feel empowered
- Async review is key: Morgan shouldn't need to pair-program to provide oversight
- Session logs become a learning artifact, not just an audit trail
- **Git-based approval model**: Taylor has triage access (can push branches), Morgan approves merges to main
- **Context-aware detection**: To know what's "agent-created", Luthien must track file creation events during the session (e.g., from tool calls like `Write` or `Bash(touch/mkdir)`). This requires persisting session state and comparing against it when destructive commands are issued.

## Future Documentation (TODO)

Items to add as this story matures:

- [ ] **Success metrics** - How do we know it's working? (warning rate decreases, review time decreases)
- [ ] **Edge cases** - Long sessions (100+ messages), multiple warnings, Morgan unavailable
  > **Real friction observed (2026-01-15):** When Morgan hasn't approved several PRs, Taylor feels blocked but hesitant to ping ("they seem busy, I don't want to bother them"). Multiple TODO.md files across branches add confusion about what to prioritize. Emotions: "stupid, why didn't I think of that" when mistakes surface in review.
- [ ] **Comparison to alternatives** - Why Luthien vs GitHub PR comments alone vs pair programming?
- [ ] **Failure modes** - What if Luthien is down? What if logs are too noisy?
- [ ] **Privacy considerations** - Who can see session logs? Retention policy?
