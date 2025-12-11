# Luthien UI/UX Exploration & Redesign Plan

**Created:** 2025-12-11
**Status:** Planning phase
**Branch:** `ux-exploration`
**Context:** Rethinking Luthien UI based on Nielsen's usability heuristics

---

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

## Recommended Next Steps

### Immediate (Today - Shower Thinking)
- [ ] Review shower questions
- [ ] Sketch ideal UX on paper/Figma
- [ ] Decide on Option A, B, or C
- [ ] Answer key questions (what's the heartbeat metric? etc.)

### Tomorrow (Pick Up Work)
1. **Rebase branch off latest main**
   ```bash
   git checkout ux-exploration
   git fetch origin
   git rebase origin/main
   ```

2. **Build prototype** (Option A recommended)
   - Create unified `/` dashboard page
   - Inline activity preview
   - Progressive disclosure for available policies

3. **Wire to real data**
   - Connect to `/admin/policy/current`
   - Connect to activity stream
   - Show real policy status

4. **Test & iterate**
   - Use it yourself (dogfood)
   - Get Jai's feedback
   - Refine based on usage

### Week 2+ (If This Works)
- Deprecate old pages (policy-config, policy-manager)
- Update navigation
- Add empty states
- Add policy switching functionality
- Polish UI/animations

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

**Last Updated:** 2025-12-11
**Next Review:** When picking up tomorrow
**Owner:** Scott
