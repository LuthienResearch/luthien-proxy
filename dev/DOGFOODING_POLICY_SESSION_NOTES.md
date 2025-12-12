# Dogfooding Policy - Session Notes

**Date:** 2025-12-12
**Branch:** `ux-exploration`
**Status:** Research/Design phase - No implementation yet

---

## What We Were Trying To Do

**Original goal:** Pick an existing policy to dogfood (use Luthien for real work)

**Problem discovered:** Existing policies aren't useful for learning:
- **NoOpPolicy** - Does nothing (boring)
- **AllCapsPolicy** - Just for testing (annoying)
- **DebugLoggingPolicy** - Redundant (Luthien already logs everything)
- **ToolCallJudgePolicy** - Duplicates Claude Code's PermissionRequest hooks

**Key insight:** Need a policy that does something **complementary** to Claude Code, not duplicative.

---

## What Luthien Captures (New Discovery)

**Database tables:**
- `conversation_events` - Every request/response, full pipeline
- `conversation_calls` - High-level call metadata
- `policy_events` - Policy decisions and modifications
- `debug_logs` - Internal debugging data

**What's logged automatically:**
- ✅ All user messages (your requests to Claude)
- ✅ All assistant responses (Claude's replies)
- ✅ Tool calls (Read, Write, Edit, Bash, etc.)
- ✅ Policy decisions (allow/block/modify)
- ✅ Format conversions (OpenAI ↔ Anthropic)
- ✅ Full payloads in JSONB (queryable!)
- ✅ Timestamps, trace IDs, call grouping

**How to query:**
```bash
# Quick peek
docker exec luthien-proxy-db-1 psql -U luthien -d luthien_control -c "
SELECT created_at, event_type, call_id
FROM conversation_events
ORDER BY created_at DESC
LIMIT 10;
"
```

See full reference: `dev/LUTHIEN_DATA_REFERENCE.md`

---

## The Aha Moment: Cross-Session vs Per-Session

### Claude Code Hooks Can:
- ✅ Approve/deny individual tool calls
- ✅ Make per-session decisions
- ✅ Work in real-time on current request
- ❌ Can't see patterns across sessions
- ❌ Can't access conversation history
- ❌ Can't track aggregate metrics over time

### Luthien Policies Can:
- ✅ See cross-session patterns
- ✅ Access full conversation history from DB
- ✅ Track aggregate metrics (commits/day, files changed/session, etc.)
- ✅ Provide async analysis (not just blocking decisions)
- ✅ Analyze request/response patterns across all sessions

**Therefore:** Best dogfooding policies leverage **cross-session data analysis**.

---

## Policy Ideas We Brainstormed

### 1. Commit Health Monitor 📊
**What it would do:**
- Track files changed since last commit
- Monitor time since last commit
- Alert thresholds: "10+ files changed, no commit = 🚩"
- Encourage "commit small, commit often" habit

**Why useful for Scott:**
- ✅ Addresses learning from Dec 11: "commit small, commit often"
- ✅ Concrete, measurable metrics
- ✅ Clear rules (easy to validate "is it working?")
- ✅ Immediate actionable feedback

**Complexity:** Medium (requires git state tracking)

---

### 2. Scope Creep Detector 🚩
**What it would do:**
- Compare original user request vs actual changes
- Detect "Would you also like me to..." patterns in responses
- Flag feature additions beyond original request
- Alert: "Request was 'fix login', but you added 5 features"

**Why useful for Scott:**
- ✅ Addresses #1 documented weakness (scope creep under pressure)
- ✅ Unique value - hooks can't do this
- ✅ Could catch anti-pattern in real-time

**Complexity:** High (semantic analysis, subjective rules)

**Challenge:** What counts as "scope creep" vs "necessary related change"?

---

### 3. Session Pattern Analyzer 🔍
**What it would do:**
- Analyze patterns across multiple sessions
- "You've worked on auth system 3 days in a row - consider pairing"
- "Last 5 sessions averaged 15 file changes (scope creep pattern?)"
- Weekly summary metrics

**Why useful for Scott:**
- ✅ Helps with retros (automates pattern detection)
- ✅ Cross-session insights
- ✅ Feeds into learning goals

**Complexity:** Medium-High (pattern detection, threshold tuning)

---

### 4. Learning Journal Generator 📝
**What it would do:**
- Auto-document what was worked on each session
- Generate weekly summaries: "5 PRs, 80% backend, 20% UI"
- Track progress toward learning goals
- Feed into retros with Jai

**Why useful for Scott:**
- ✅ Reduces friction for documentation
- ✅ Helps with progress tracking
- ✅ Complements existing retro process

**Complexity:** Medium (text summarization, categorization)

---

### 5. Retro Generator (The Meta Policy) 🤖
**What it would do:**
- Read conversation history from DB
- Analyze Scott's sessions (not just individual requests)
- Detect patterns: commit frequency, debugging loops, scope creep
- Generate retro notes automatically

**Why useful for Scott:**
- ✅ Automates what he's currently doing manually (asking Claude instances to review conversations)
- ✅ Leverages cross-session data
- ✅ Could inform other policy designs

**Complexity:** High (requires sophisticated analysis)

---

## Current Manual Workflow (To Automate)

**What Scott does now:**
1. Ask multiple Claude instances to review old conversations
2. Extract patterns manually
3. Use findings as input for retro
4. Design policies based on retro insights

**What Luthien could do:**
1. All conversations already logged in DB
2. Policy queries conversation_events automatically
3. Generates retro insights in real-time or at session end
4. Feeds directly into policy requirements

---

## Key Learnings

### 1. Luthien's Superpower is Cross-Session Data
- Claude Code sees one session at a time
- Luthien sees all sessions, can track patterns over days/weeks
- Perfect for: habit tracking, pattern detection, aggregate metrics

### 2. Good Dogfooding Policies Should:
- ✅ Leverage cross-session data (not per-request decisions)
- ✅ Address Scott's documented weaknesses (scope creep, commit frequency, etc.)
- ✅ Provide insights Claude Code hooks can't
- ✅ Be observational first (don't block, just inform)

### 3. Implementation Complexity Spectrum
- **Simple:** Commit counter, file change tracker
- **Medium:** Pattern detection, threshold alerts
- **Complex:** Semantic analysis, scope creep detection, auto-retros

---

## Next Steps (When Picking This Up)

### 1. Choose One Policy to Design
Pick based on:
- What would be most immediately useful?
- What addresses your biggest weakness?
- What's achievable in beach mode?

**Recommendation:** Start with **Commit Health Monitor** (simpler) or **Retro Generator** (automates current manual process)

### 2. Define Requirements
For chosen policy, answer:
- What specific data does it need from conversation_events?
- What are the alert thresholds?
- When does it provide feedback (real-time, end of session, daily summary)?
- What does "success" look like?

### 3. Design Before Building
- Sketch the rules/logic
- Define example scenarios (what should trigger what)
- Consider edge cases
- **Don't implement yet** - validate design first

### 4. Prototype Approach
- Start with SQL queries to validate data exists
- Build simple version first (e.g., just count commits)
- Add complexity incrementally
- Ship observational version before blocking version

---

## Open Questions to Answer

1. **Commit Health Monitor:**
   - How to track git state across sessions? (Shell out to git? Parse git events?)
   - What's a healthy commit frequency for Scott's workflow?
   - Alert in real-time or daily summary?

2. **Scope Creep Detector:**
   - How to define "scope creep" objectively?
   - Compare request text vs... what? (file changes? tool calls? response suggestions?)
   - How to avoid false positives when related changes are necessary?

3. **General:**
   - Should policies block or just inform?
   - Real-time alerts vs end-of-session summary?
   - How to make feedback actionable, not annoying?

---

## References

- `dev/LUTHIEN_DATA_REFERENCE.md` - Database schema and query examples
- `dev/ux-exploration.md` - UX convictions and design principles
- `/Users/scottwofford/dev/CLAUDE.md` - Documented weaknesses and learnings
- PR #101 retro - Example of scope creep pattern to analyze

---

## Blocked By / Dependencies

**Nothing blocking!** Ready to design when you return.

**Already have:**
- ✅ Luthien running and logging data
- ✅ Understanding of what's captured
- ✅ Clear policy ideas
- ✅ Scott's documented weaknesses as requirements

**Need to decide:**
- Which policy to build first
- Design requirements before implementing

---

**Next session:** Pick one policy, define requirements, sketch the design. Don't implement yet!
