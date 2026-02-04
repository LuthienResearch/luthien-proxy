# Policy Configuration UI v2 - Spec

**Created:** 2026-02-03
**Status:** Draft - gathering requirements
**Context:** Redesign based on user feedback (Zac/Counterweight) + Nielsen heuristics

---

## Zac's Requirements (Counterweight Call, Feb 3 2026)

### Primary Pain Point
> "The too autonomous thing where it will go and like also refactor some other thing which I didn't want... when Opus 4.5 decides it should go and edit other files and make some big codebase improvement which potentially adjusts stuff I don't want adjusted"

### Desired Behavior
1. **Block scope-creepy edits silently** - Don't interrupt workflow
2. **Show blocked items at END of turn** - "When it's finished and pauses for user input, that's when I want to see what got rejected"
3. **Async over sync** - "If I'm monitoring my code, I can see what it said anyway"

### Notification Preferences
- ❌ NOT a custom website to visit ("I might not visit that")
- ✅ Email or Slack channel
- ✅ Inline at end of Claude's turn
- ✅ Summary of what was blocked

### UI Feedback (from demo)
> "So much it's kind of overwhelming and hard to use"

**What he wants:**
- "Here's how you can test with Claude Code" - right on policy config page
- Pre-configured with **meaningful defaults** (not "AllCaps" but "block jailbreaks")
- Hide things users don't need (API base, API key can be hidden)
- Obvious single parameter to adjust
- **Simplicity is key** - "I don't need to think about it"

**What worked:**
- Policy config page concept "totally makes sense"
- Being able to see/edit policies is useful

### Trust/Adoption Flow
1. Find out about thing
2. Go to GitHub (open source = trust)
3. **"If I run the GitHub readme and it doesn't work, I'll probably never touch it again"**

### Earlier Feedback (Slack, Jan 30-31)
Policy that evaluates original prompt against final agent message:
> "check that your final reasoning matches user's original request"

---

## Problems with Current UI

### From PR #123 (Dec 2025)
1. Wizard had locked forward-only navigation
2. Forced flow through all steps
3. Bespoke nav header inconsistent with other pages

### From UX Analysis (Jan 2026)
1. **No global system status** - Can't tell if Luthien is working without clicking into Activity Monitor
2. **Inconsistent navigation** - Each page has different nav links
3. **No undo** - Can't revert to previous policy
4. **No confirmation** - Policy switches immediately without warning

---

## Design Principles (Nielsen's Heuristics)

| Heuristic | Application |
|-----------|-------------|
| **1. Visibility of system status** | Show: Is Luthien running? Which policy active? Is it working? |
| **3. User control and freedom** | Easy to switch policies, undo changes |
| **4. Consistency** | Match nav/styling across all pages |
| **5. Error prevention** | Confirm before policy switch |
| **6. Recognition > recall** | Don't make users remember which page does what |
| **8. Minimalist design** | Hide complexity until needed |

---

## Key Convictions

### 1. Show Proof of Life
After enabling policy, show:
```
✅ Policy activated!
Waiting for first request... ⏳

(Once request comes through)
✅ Policy is working! Just blocked a tool call
[View details →]
```

### 2. Quick Switch vs Configure
Two modes:
- **Quick switch:** One click + confirmation
- **Configure:** Full form for new settings

### 3. Global Status Bar
Persistent header across all pages:
```
🟢 Luthien operational | Policy: SimpleJudgePolicy | 47 requests today
```

### 4. Unified Navigation
```
Overview | Policies | Activity | History    [Sign Out]
```

---

## Proposed Layout

```
┌─ Luthien Proxy ──────────── 🟢 Operational ─────────────┐
│   Overview | Policies | Activity | History   [Sign Out]  │
└──────────────────────────────────────────────────────────┘

┌─ Current Policy ─────────────────────────────────────────┐
│  SimpleJudgePolicy                            🟢 Active  │
│  Enabled 2h ago • 47 requests • 3 blocked                │
│                                                          │
│  Recent Activity                                         │
│  ├─ 15:32  🛑 BLOCKED  rm -rf /                         │
│  ├─ 15:31  ✅ ALLOWED  ls                               │
│  └─ [View all →]                                        │
│                                                          │
│  [Configure] [Switch Policy] [Test]                      │
└──────────────────────────────────────────────────────────┘

┌─ Available Policies ─────────────────────────────────────┐
│  ┌─────────────────┐  ┌─────────────────┐               │
│  │ NoOpPolicy      │  │ DeSlopPolicy    │               │
│  │ Pass-through    │  │ Remove slop     │               │
│  │ [Activate]      │  │ [Activate]      │               │
│  └─────────────────┘  └─────────────────┘               │
└──────────────────────────────────────────────────────────┘
```

---

## Success Metrics

1. **Time-to-value < 2 min** - New user sees policy working
2. **Daily usage** - Scott actually opens UI to check status
3. **Reduced "is it working?" questions**

---

## Open Questions

1. Should config be inline or modal?
2. How prominent should test chat be?
3. What's the minimum info needed on the dashboard?
4. **Notification delivery** - Email? Slack? End-of-turn inline?
5. **Default policy** - What's a meaningful default? (Zac: "block jailbreaks" > "AllCaps")

---

## Key Takeaways from Zac Call

1. **Simplicity wins** - Hide complexity, show only what matters
2. **Meaningful defaults** - Pre-configure something useful, not a toy example
3. **Clear next step** - "Test with Claude Code" should be obvious from policy page
4. **Async notifications** - Show blocked items at end of turn, not during
5. **GitHub readme is make-or-break** - If it doesn't work first try, users bounce

---

## Next Steps

- [x] Fill in Zac's requirements from call
- [ ] Simplify policy config page (hide API base/key, fewer options visible)
- [ ] Add "Test with Claude Code" instructions to policy page
- [ ] Choose meaningful default policy
- [ ] Design end-of-turn notification format
- [ ] Create wireframes/mockups
- [ ] Review with Jai
