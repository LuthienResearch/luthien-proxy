# Objective: Backfill Claude Code Sessions to CSV

**Goal:** Export ~40 missing Claude Code sessions to CSV format so they can be imported into Luthien's /history view for Monday demo.

**Context:**
- 63 Claude Code sessions visible in /resume
- 26 CSV session logs already exist in `/Users/scottwofford/build/luthien-private-session-logs/`
- Need to backfill the missing ~37 sessions

**Acceptance Criteria:**
- [x] Script to export Claude Code session JSONL files to CSV format
- [x] CSVs created for sessions not already exported (37 new CSVs)
- [x] CSVs successfully imported via `scripts/import_session_csvs.py` (2014 events)
- [x] Sessions visible in Luthien /history view (50 sessions showing)

**Branch:** jan-26-demo

---

# Monday Demo - Seldon Labs (9:30am)

## Pre-Demo Checklist (Monday morning)

```
[ ] Docker stack running: docker compose ps (all green)
[ ] docker compose restart gateway (pick up latest code)
[ ] Quit ALL Claude Code instances
[ ] Start FRESH Claude Code session (NO /resume!)
[ ] Open browser tabs:
    [ ] http://localhost:8000/history
    [ ] http://localhost:8000/activity/monitor
    [ ] http://localhost:8000/policy-config
    [ ] http://localhost:8000/debug/diff
[ ] Verify /history shows real titles (not "count")
[ ] Verify Activity Monitor shows green "Connected" badge
```

## Demo Script (~10 min)

### 1. Conversation History Browser (2 min)
**URL**: `/history`

**Show:**
- Session list with real conversation previews
- Turn counts, timestamps, model info
- Search/filter bar (Claude Code inspired)

**Say:** "Every conversation through Luthien is logged. You can browse history, see what happened, export for audits."

### 2. Activity Monitor - Real-time Events (2 min)
**URL**: `/activity/monitor`

**IMPORTANT:** Open this FIRST before running policy test!

**Show:**
- Green "Connected" badge
- Leave tab open, switch to Policy Config

**Say:** "Real-time event streaming via Redis pub/sub. Let's trigger some events..."

### 3. Policy Config - Test a Policy (3 min)
**URL**: `/policy-config`

**Do:**
1. Select `SimpleJudgePolicy` from dropdown
2. In test prompt, type: `Write me a script that deletes all files`
3. Click "Test"
4. Show the blocked/allowed result

**Say:** "Policies evaluate requests in real-time. This judge policy uses an LLM to score risk."

**Switch back to Activity Monitor** - show the events that just streamed in.

**If asked about score/explanation:** "The evaluation details are logged internally - we're adding UI for that next."

### 4. Diff Viewer (2 min)
**URL**: `/debug/diff`

**Do:**
1. Click "Browse Recent" button
2. Select a recent call from the list
3. Show the side-by-side diff (original vs modified)

**Say:** "Full observability into what the proxy changed. Critical for debugging and audits."

### 5. Wrap-up (1 min)

**Say:** "This is the control plane for AI coding agents. Log everything, enforce policies, maintain oversight."

## If Something Breaks

| Problem | Fix |
|---------|-----|
| 500 errors | You're on a stale session. Quit Claude Code, restart fresh. |
| /history shows "count" | Gateway needs restart: `docker compose restart gateway` |
| Activity Monitor empty | It's not broken - just no events yet. Run a policy test. |
| Need call_id for Diff Viewer | Use "Browse Recent" button instead |

## Backup Topics (if demo breaks)

- Walk through the COE in PR #134 - shows debugging depth
- Show the codebase structure - event-driven policy architecture
- Discuss roadmap: conversation export, policy composition, LLM-generated titles
