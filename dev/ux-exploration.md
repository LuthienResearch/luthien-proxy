# Luthien UI/UX Exploration & Redesign Plan

**Created:** 2025-12-11
**Last Updated:** 2026-01-20
**Status:** Planning phase
**Branch:** `ux-exploration`
**Context:** Rethinking Luthien UI based on Nielsen's usability heuristics

---

## Design Principles (Nielsen's 10 Heuristics)

Applied to Luthien:

1. **Visibility of system status** ⭐ PRIMARY FOCUS
   - Show: Is Luthien running? Which policy is active? Is it working?

2. **Match between system and real world**
   - Use domain language: "policies" not "configurations"
   - Show concrete examples of blocking/allowing

3. **User control and freedom**
   - Easy to switch policies, undo changes

4. **Consistency and standards**
   - Follow web app conventions

5. **Error prevention**
   - Warn before destructive actions

6. **Recognition rather than recall**
   - Don't make users remember which page does what

7. **Flexibility and efficiency**
   - Shortcuts for power users, simple for novices

8. **Aesthetic and minimalist design** ⭐ SECONDARY FOCUS
   - Hide complexity until needed

9. **Help users recognize/recover from errors**
   - Clear error states with recovery actions

10. **Help and documentation**
    - Embedded guidance, not separate docs

---

# Current State Analysis (2026-01-20)

## UI Inventory

| Route | Page | Purpose |
|-------|------|---------|
| `/` | index.html | Landing page with endpoint links |
| `/activity/monitor` | activity_monitor.html | Real-time event stream |
| `/policy-config` | policy_config.html | Policy configuration & testing |
| `/debug/diff` | diff_viewer.html | Request/response diff viewer |
| `/history` | history_list.html | Conversation session browser |
| `/history/session/{id}` | history_detail.html | Conversation detail view |

**Changes since Dec 11:**
- `/policy-manager` removed (merged into `/policy-config`)
- `/history` and `/history/session/{id}` added (Conversation History Viewer)
- Landing page updated with Auth Required badges and Quick Start section

---

## Analysis by Heuristic

### 1. Visibility of System Status ⭐ PRIMARY

| Page | Status Visibility | Rating |
|------|-------------------|--------|
| Landing (`/`) | None - just links | ❌ |
| Activity Monitor | Connected/disconnected indicator with pulse | ✅ |
| Policy Config | "Active Policy" banner shows current policy | ✅ |
| Diff Viewer | None | ❌ |
| History List | Total sessions count | ⚠️ |
| History Detail | Turn count, interventions, models | ✅ |

**Gap**: No **global system health indicator** visible across pages. User cannot answer "Is Luthien working?" without clicking into Activity Monitor.

**Recommendation**: Add persistent status bar to all pages:
```
🟢 Luthien operational | Policy: ToolCallJudgePolicy | 47 requests today
```

---

### 2. Match Between System and Real World

| Element | Language Used | Rating |
|---------|--------------|--------|
| Policies | "Active Policy", "Available Policies" | ✅ |
| Conversations | "Sessions", "Turns", "Messages" | ✅ |
| Events | Technical: `policy.stream.start`, `response.original_chunk` | ⚠️ |
| Actions | "Policy interventions" (clear), "Auth Required" (clear) | ✅ |

**Gap**: Activity monitor event types are technical (`policy.stream.start`). New users may not understand what they mean.

**Recommendation**: Add human-readable event summaries alongside technical type.

---

### 3. User Control and Freedom

| Feature | Present | Rating |
|---------|---------|--------|
| Sign out link | On every page | ✅ |
| Back navigation | History detail → list | ✅ |
| Undo policy change | Not available | ❌ |
| Clear activity | Button available | ✅ |
| Browse recent calls | In diff viewer | ✅ |

**Gap**: No undo for policy activation. No "revert to previous policy" option.

**Recommendation**: Add "Previous Policy" quick-switch or confirmation dialog.

---

### 4. Consistency and Standards

| Element | Consistency | Rating |
|---------|-------------|--------|
| Dark theme | All pages use #0a0a0a / #141414 | ✅ |
| Typography | System fonts, consistent sizing | ✅ |
| Cards/sections | Similar border-radius, padding | ✅ |
| Badges | Consistent "Auth Required" on landing | ✅ |
| Navigation | **Inconsistent across pages** | ❌ |

**Gap**: Each page has different navigation links in header:
- Landing: None (just content)
- Activity Monitor: Sign Out only
- Policy Config: Activity Monitor, Diff Viewer, Sign Out
- Diff Viewer: Sign Out only
- History: Sign Out only

**Recommendation**: Add consistent navigation bar across all authenticated pages:
```
Overview | Policies | Activity | History | Settings    [Sign Out]
```

---

### 5. Error Prevention

| Scenario | Prevention | Rating |
|----------|------------|--------|
| Switch policy | No confirmation | ❌ |
| Clear activity | Immediate (no undo) | ⚠️ |
| Invalid call_id | Clear error message | ✅ |
| Auth required | Redirect to login | ✅ |

**Gap**: No confirmation before switching active policy.

**Recommendation**: Add confirmation modal before policy switch.

---

### 6. Recognition Rather Than Recall

| Feature | Recognition Support | Rating |
|---------|---------------------|--------|
| Landing page | Link directory - must remember | ❌ |
| Policy list | Shows descriptions inline | ✅ |
| History sessions | Shows preview stats | ✅ |
| Navigation | Must remember URLs/pages | ❌ |

**Gap**: Landing page is a link directory requiring users to remember which page does what. No global navigation.

**Recommendation**: Add persistent nav bar OR transform landing page into dashboard.

---

### 7. Flexibility and Efficiency of Use

| Feature | Power User Support | Rating |
|---------|-------------------|--------|
| Activity filtering | ID filter, event type dropdown | ✅ |
| Policy test | Inline test panel | ✅ |
| Diff viewer | Direct call_id input + browse | ✅ |
| Keyboard shortcuts | None | ❌ |
| Quick Start section | Links on landing page | ✅ |

**Gap**: No keyboard shortcuts for common actions.

---

### 8. Aesthetic and Minimalist Design ⭐ SECONDARY

| Page | Information Density | Rating |
|------|---------------------|--------|
| Landing | Text-heavy, many sections | ⚠️ |
| Activity Monitor | Clean, collapsible events | ✅ |
| Policy Config | Well-organized panels | ✅ |
| Diff Viewer | Side-by-side, clear | ✅ |
| History List | Card-based, scannable | ✅ |
| History Detail | Message-based, organized | ✅ |

**Gap**: Landing page shows every endpoint including technical APIs.

**Recommendation**: Simplify landing to show only UIs + Quick Start.

---

### 9. Help Users Recognize, Diagnose, and Recover from Errors

| Scenario | Error Handling | Rating |
|----------|---------------|--------|
| Network failure | Error message shown | ✅ |
| 403 unauthorized | Redirect to login | ✅ |
| Session not found | Clear error message | ✅ |
| Policy activation failure | Error in status panel | ✅ |

**Overall**: Good error handling.

---

### 10. Help and Documentation

| Feature | Present | Rating |
|---------|---------|--------|
| Endpoint descriptions | On landing page | ✅ |
| Policy descriptions | In config panel | ✅ |
| Onboarding flow | None | ❌ |
| Tooltips | None | ❌ |

**Gap**: No onboarding for new users.

---

## Summary Scorecard (Jan 2026)

| Heuristic | Score | Priority |
|-----------|-------|----------|
| 1. Visibility of system status | ⚠️ Partial | **HIGH** |
| 2. Match system ↔ real world | ✅ Good | LOW |
| 3. User control and freedom | ⚠️ Partial | MEDIUM |
| 4. Consistency and standards | ⚠️ Partial | **HIGH** |
| 5. Error prevention | ⚠️ Partial | MEDIUM |
| 6. Recognition rather than recall | ❌ Poor | **HIGH** |
| 7. Flexibility and efficiency | ✅ Good | LOW |
| 8. Aesthetic and minimalist design | ✅ Good | LOW |
| 9. Help with errors | ✅ Good | LOW |
| 10. Help and documentation | ⚠️ Partial | MEDIUM |

---

## Top 3 Recommendations (Jan 2026)

### 1. Add Global System Status (Heuristics 1, 4)

Add a persistent header across all authenticated pages showing:
- Gateway health status (🟢/🔴)
- Active policy name
- Request count (today or last hour)

### 2. Unify Navigation (Heuristics 4, 6)

Replace inconsistent per-page navigation with a global nav bar:
```
[Luthien Logo] Overview | Policies | Activity | History    [Sign Out]
```

### 3. Transform Landing into Dashboard (Heuristics 1, 6, 8)

Replace endpoint directory with a dashboard showing:
- Current policy status
- Recent activity preview
- Last few sessions

---

# Original Analysis (2025-12-11)

## Current State Analysis

### Existing Pages
1. **`/` (Landing)** - List of links, no status/overview
2. **`/activity/monitor`** - Real-time event stream (separate page)
3. **`/policy-config`** - 3-step wizard (Select → Enable → Test)
4. **`/policy-manager`** - Simple read-only list (just built, pre-redesign)
5. **`/debug/diff`** - Diff viewer

### Core Problems
- **Fragmented experience** - Multiple pages with unclear relationships
- **No system status visibility** - Can't tell at a glance if Luthien is working
- **Confusing navigation** - "Policy Manager" vs "Policy Config" overlap
- **No "aha moment"** - New users don't immediately see value
- **Hard to verify policies work** - Activity is separate from policy view

---

## Key Convictions & Assertions

### 1. The Dashboard IS the Landing Page
**Problem:** Landing page is just links. No status visibility.

**Solution:** Make `/` a status dashboard showing:
- ✅ Is Luthien working?
- 🎯 What policy is active?
- 📊 Is the policy doing anything? (recent activity)

### 2. One Policy Page, Not Three
**Problem:** Confusion between "manager", "config", and separate activity monitor.

**Solution:** Merge into unified `/policies` page with progressive disclosure:
```
Current Policy (always visible)
  ↓
Available Policies (collapsed by default)
  ↓
Recent Activity (inline preview)
  ↓
[View Full Activity] → Separate detail view
```

### 3. Activity = Proof of Life
**Problem:** Users can't tell if policy is working without clicking around.

**Solution:** Show inline activity preview on policies page:
```
ToolCallJudgePolicy - 🟢 Active
├─ 15:32 Blocked: rm -rf /
├─ 15:31 Allowed: ls
└─ [View all activity] →
```

### 4. System Health = Persistent Context
**Solution:** Header status indicator:
```
🟢 All systems operational (47 req/min)
```

### 5. New User Journey is Linear, Then Freeform
**Solution:** Empty state with guided setup:
```
No Policy Active
  ↓
[Get Started] → Wizard
  ↓
Choose policy → Activate → See it work
```

### 6. Config Should Feel Like Tweaking
**Problem:** Heavy wizard for simple policy switching.

**Solution:** Two modes:
- **Quick switch:** One click + confirmation
- **Configure new:** Full wizard/modal

### 7. Observable Proof > Abstract Status
**Solution:** After enabling, show:
```
✅ Policy activated!
Waiting for first request... ⏳
[Send test request] ← Optional

(Once request comes through)
✅ Policy is working! Just blocked a tool call
[View details →]
```

### 8. Navigation Reflects User Goals
**Current:** Activity Monitor | Policy Manager | Policy Config | Diff Viewer

**Better:** Overview | Policies | Activity | Settings

### 9. Prevent Errors Proactively
**Solution:** Impact warnings:
```
Switching to NoOpPolicy
⚠️ This will affect 3 in-flight requests
[Cancel] [Switch Anyway]
```

### 10. Show, Don't Tell the Value Prop
**Solution:** Landing page example:
```
Before Luthien       After Luthien
Agent: rm -rf /      Agent: rm -rf /
✅ Executed          ❌ BLOCKED by policy
```

---

## Shower Questions for Scott

### Strategic
1. What's the ONE thing a new user needs to understand in 10 seconds?
2. When dogfooding, what's your most common action?
3. If you had to delete 2 of these 3 pages, which would you keep?
4. What does "success" look like for a first-time user?

### Architecture
5. Should `/` be marketing page → dashboard, or straight to dashboard?
6. What's the relationship between "policy" and "activity"?
7. When someone enables a policy, what happens next?

### UX
8. What makes Luthien feel "trustworthy"?
9. What's scarier: false positives or false negatives?
10. If demoing to investor, what do you show first?

### Technical
11. How show "working" when policy enabled but no requests yet?
12. What's the "heartbeat" metric? (requests/min, blocks, evaluations?)
13. When show detailed logs vs summaries?

### Future-Proofing
14. When multiple policies supported, what changes?
15. UI with 10+ built-in policies - how organize?
16. How scale from 1 user → team → enterprise?

### Simplification
17. What can you delete entirely?
18. What's smallest v1 that proves value?
19. If only 3 pieces of info, what are they?
20. What would make you use UI daily vs CLI/API?

---

## Proposed Information Architecture

### Option A: Single Unified Page
```
┌─ Policies (/) ──────────────────────────┐
│                                          │
│ Current Active Policy                    │
│ ┌──────────────────────────────────────┐│
│ │ ToolCallJudgePolicy         🟢 Active││
│ │ Enabled 2h ago by Scott              ││
│ │ ↳ 47 requests, 3 calls blocked       ││ ← Activity proof
│ │                          [Configure] ││ ← On hover
│ └──────────────────────────────────────┘│
│                                          │
│ Recent Activity                          │
│ ├─ 15:32 Blocked: rm -rf /              │
│ ├─ 15:31 Allowed: ls                    │
│ └─ [View all activity →]                │
│                                          │
│ Available Policies          ▼ collapsed │ ← Progressive
│                                          │
│ [Switch Policy] [Configure New]          │
└──────────────────────────────────────────┘
```

**Pros:**
- Everything in one place
- Clear hierarchy
- Obvious next actions

### Option B: Dashboard + Modals
```
Main page = Dashboard (read-only)
[Change Policy] → Opens modal
[Configure New] → Opens wizard modal
```

**Pros:**
- Simple default view
- Advanced features on demand
- Familiar modal pattern

### Option C: Dashboard Home + Deep Tools
```
Overview (/) = Dashboard landing
  ↓ Current policy status
  ↓ Quick actions
  ↓ [Advanced Setup] → Full wizard

Policies (/policies) = Deep policy management
Activity (/activity) = Detailed logs/monitoring
```

**Pros:**
- Separation of concerns
- Simple by default, powerful when needed

---

## Design Mockup Ideas

### Unified Dashboard Concept
```
┌─ Luthien Proxy ──────────── 🟢 All systems operational ─┐
│   Overview | Policies | Activity | Settings   [Sign Out] │
└────────────────────────────────────────────────────────────┘

┌─ Current Policy ─────────────────────────────────────────┐
│                                                           │
│  ToolCallJudgePolicy                          🟢 Active  │
│  Evaluates tool calls with judge LLM                     │
│                                                           │
│  ┌─ Status ──────────────────────────────────────────┐  │
│  │ Enabled:  2 hours ago by Scott                    │  │
│  │ Activity: 47 requests processed                   │  │
│  │ Blocked:  3 dangerous tool calls                  │  │
│  └───────────────────────────────────────────────────┘  │
│                                                           │
│  Recent Activity                                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 15:32  🛑 BLOCKED  rm -rf / (dangerous command)   │  │
│  │ 15:31  ✅ ALLOWED  ls (safe command)              │  │
│  │ 15:28  🛑 BLOCKED  curl malicious.com (URL check) │  │
│  └───────────────────────────────────────────────────┘  │
│                                                           │
│  [View Full Activity] [Configure Policy] [Switch Policy] │
└───────────────────────────────────────────────────────────┘

┌─ Quick Actions ──────────────────────────────────────────┐
│  [📝 Send Test Request]  [📊 View Metrics]  [⚙️ Settings]│
└───────────────────────────────────────────────────────────┘
```

---

## Success Metrics

**How we'll know the redesign works:**

1. **New user time-to-value < 2 minutes**
   - Land on page → See policy working → Understand value

2. **Reduced support questions**
   - "How do I know if it's working?" → Observable on dashboard
   - "Where do I configure policies?" → One obvious place

3. **Daily usage**
   - Scott actually opens UI to check status
   - Not just CLI/logs

4. **Faster iteration**
   - Quick policy switching for testing
   - Inline test functionality

---

## Technical Notes

### Current Limitations to Design Around
- Only one policy can be active at a time (Jai confirmed)
- Activity monitor uses Redis pub/sub for real-time streaming
- Policy configuration stored in DB + file fallback
- Authentication required for all admin pages

### API Endpoints Available
- `GET /admin/policy/current` - Current active policy
- `GET /admin/policy/list` - Available policies
- `POST /admin/policy/set` - Enable a policy
- `GET /activity/stream` - SSE stream of events

### Files to Create/Modify
- `src/luthien_proxy/static/index.html` - New dashboard
- `src/luthien_proxy/ui/routes.py` - Update routes
- Potentially deprecate:
  - `policy_config.html` (or repurpose as modal)
  - `policy_manager.html` (merge into dashboard)

---

## Notes & Considerations

### Keep in Mind
- Scott learns best by doing, not reading
- Prefers "show me once, let me try" over tutorials
- Goal: Build small UI features autonomously
- Beach mode = keep scope tiny, ship fast

### Red Flags to Avoid
- 🚩 Scope creep - Adding features beyond core dashboard
- 🚩 Perfectionism - Polish before basic functionality works
- 🚩 Over-research - Reading docs instead of building
- 🚩 Random debugging - Be systematic if issues arise

### Remember
- Commit small, commit often
- Run `./scripts/dev_checks.sh` before committing
- This is a learning exercise - better to ship rough draft than perfect concept

---

**Owner:** Scott
