# Spec: Retrospective Search / Decision Point Detection

**Status**: Draft - needs requirements brainstorm
**Created**: 2025-12-12
**Context**: Session retrospective for PR #101

---

## Problem Statement

When doing retrospectives on AI-assisted coding sessions, users need to find key decision points from past conversations. Currently this requires:
1. Manually searching through Claude Code session files
2. Relying on summaries that lose important detail
3. Asking other Claude instances to review old conversations

Luthien already captures full conversation logs. We need a way to query them for retrospective analysis.

---

## User Story

> As a developer using AI coding assistants through Luthien, I want to search my past conversations for decision points so I can do retrospectives and improve my workflow.

---

## What Luthien Already Does

- [x] Captures full request/response bodies in `conversation_events`
- [x] Stores metadata (timestamps, call_id, event_type)
- [x] Persists across sessions

---

## Proposed Feature: Decision Point Search

### MVP (Manual Query)

Simple SQL/API to search conversation history:

```sql
SELECT created_at, event_data
FROM conversation_events
WHERE event_data::text ILIKE '%<search_term>%'
ORDER BY created_at DESC;
```

**Useful search patterns:**
- `should I` - user asking for advice
- `best practice` - seeking guidance
- `my recommendation` - Claude giving advice
- `let's do` / `let's go with` - user making decision

### V1: Decision Point Tagging

A policy that watches conversations and auto-tags decision points:

**Detection patterns:**
| Pattern | Type | Example |
|---------|------|---------|
| User: "should I..." | `advice_request` | "should I break up into multiple PRs?" |
| Claude: "I recommend..." | `recommendation` | "I recommend keeping as one PR" |
| User: short affirmative after recommendation | `decision_made` | "yes", "let's do that", "sounds good" |
| User: "what's best practice" | `guidance_request` | "what's best practice for X?" |

**Storage:**
```sql
INSERT INTO policy_events (event_type, event_data, call_id)
VALUES ('decision_point', {
  'pattern_type': 'recommendation',
  'summary': 'Claude recommended keeping PR as one',
  'user_response': 'as is, let\'s wrap it up',
  'context_snippet': '...'
}, call_id);
```

**Query:**
```sql
SELECT * FROM policy_events
WHERE event_type = 'decision_point'
AND created_at BETWEEN '2025-12-10' AND '2025-12-11';
```

### V2: Retrospective UI

- Web UI to browse decision points
- Filter by date range, pattern type
- Show conversation context around each decision
- Link to related PRs/commits if detectable

---

## Open Questions (for brainstorm)

1. **What patterns indicate a "decision point"?**
   - Need more examples from real sessions
   - False positive tolerance?

2. **How much context to store?**
   - Just the decision exchange?
   - N messages before/after?
   - Full conversation?

3. **Should users be able to manually tag decisions?**
   - Slash command like `/bookmark this is important`?
   - Or fully automatic?

4. **Integration with existing tools?**
   - Export to markdown for retrospective docs?
   - Link to GitHub PRs/issues?
   - Slack/Discord notifications for key decisions?

5. **Privacy/retention considerations?**
   - How long to keep decision logs?
   - Who can query them?

6. **What makes a retrospective useful?**
   - Just finding decisions isn't enough
   - Need outcome tracking (did the decision work out?)
   - PR #101 example: decision was "keep as one PR", outcome was "flagged for quality issues unrelated to scope"

---

## Related Context

- **PR #101 retrospective**: User asked "should I break up into multiple PRs?", Claude recommended keeping as one. PR was later flagged for quality issues (duplicate import, placeholder text) - unrelated to scope.
- **Key insight**: The issues weren't about PR splitting, but about pre-PR quality checks. A "decision point" feature would help identify this pattern across sessions.
- **Session log**: `dev/retrospective-2025-12-10-session-log.md`

---

## Next Steps

1. [ ] Brainstorm requirements with Scott
2. [ ] Review more session logs for decision point patterns
3. [ ] Decide MVP scope (manual SQL vs API vs UI)
4. [ ] Prototype detection patterns on existing logs
