# Policies Page Redesign

## Overview

Refactor the policies page from a chain-builder-first UI to a template-focused,
single-policy-first experience with richer categorization, simplified configuration,
and a Before/After test harness.

Three-panel layout is preserved (Available / Proposed / Active) but each panel's
purpose and UX shifts significantly.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Chain support | De-emphasized, not removed | Single-policy is the default; chains appear only when user adds a second policy |
| Category source | `category` class attribute on `BasePolicy` | Keeps source of truth with the policy implementation; discovery already introspects classes |
| Config forms | Auto-generated from Pydantic schema (improved styling) | Scales automatically; no per-policy maintenance |
| Before/After test | Single LLM call, capture pre/post policy | Cheapest, most honest comparison; shows exactly what the policy changed |
| Before/After scope | Response diff only | Most policies modify responses; request diffs can be added later if needed |
| Judge credential default | `user_credentials` (OAuth passthrough) | Users on Claude subscriptions shouldn't need separate API keys for policy judge calls |

## Panel 1: Available (Template Catalog)

### Category System

`BasePolicy` gains a class attribute:

```python
class BasePolicy:
    category: str = "advanced"  # default for uncategorized
```

Policies override:

```python
class StringReplacementPolicy(BasePolicy, AnthropicHookPolicy):
    category = "simple_utilities"
```

Categories (display order):

| Key | Display Label | Example Policies |
|-----|--------------|-----------------|
| `simple_utilities` | Simple Utilities | StringReplacement, AllCaps |
| `active_monitoring` | Active Monitoring & Editing | SimpleLLMPolicy, ToolCallJudge |
| `fun_and_goofy` | Fun & Goofy | Future novelty policies |
| `advanced` | Advanced / Debugging | DebugLogging, DogfoodSafety |

### UI Behavior

- Collapsible accordion sections per category
- Categories with no visible policies are hidden
- Filter/search input filters across all categories
- [+] button adds policy to Proposed panel
- Hidden-policies toggle still works for internal policies

### API Change

`/api/admin/policy/list` response adds `category` field per policy.

## Panel 2: Proposed (Configure & Activate)

### Single-Policy Default

When user clicks [+] on a policy:
- Policy name at top with badges (e.g., "Auto-Retry")
- Auto-generated config form from Pydantic schema
- Credential indicator for judge-calling policies: "Uses your Claude credential"
  with collapsed override to select a server key
- "Activate" button at bottom

### Credential Handling

- `auth_provider` defaults to `"user_credentials"` in the UI
- Raw field hidden from form; replaced with friendlier UI element
- Advanced override to pick server-stored key (collapsed)

### Chain Support (De-emphasized)

- If a policy is already in Proposed and user clicks [+] on another, it becomes a chain
- Small "Chain: N policies" indicator, expandable to reorder/remove
- Composes into `MultiSerialPolicy` on activation
- Zero chain UI clutter in the single-policy path

### On Activation

- POST `/api/admin/policy/set` (existing endpoint)
- Policy moves to Active panel
- Proposed panel returns to empty state

## Panel 3: Active (Test Harness with Before/After)

### Display

- Policy name and config summary (read-only)
- "Deactivate" option (switches to NoOp)

### Test Harness

- Text input for test prompt
- Model selector dropdown (from `/api/admin/models`)
- TEST button
- Before/After response display:
  - **Before:** Raw LLM response pre-policy
  - **After:** Response after policy processing
  - Side-by-side layout (stacked on narrow screens)

### Implementation: Capturing Before/After

- Pipeline hook to snapshot response before policy modifies it (test mode only)
- Test endpoint returns `{before: "...", after: "..."}` instead of `{content: "..."}`
- For streaming policies, buffer full response before showing diff
- Normal proxy behavior unaffected

### Credential for Test Calls

- Defaults to same credential strategy as active policy
- Existing credential source selector as override

## Backend Changes Required

1. **`BasePolicy`**: Add `category: str = "advanced"` class attribute
2. **Policy classes**: Set `category` on each policy
3. **Policy discovery** (`policy_discovery.py`): Include `category` in discovered metadata
4. **Admin API** (`/api/admin/policy/list`): Return `category` in response
5. **Test endpoint** (`/api/admin/test/chat`): Add `capture_before_after` mode
   that returns pre-policy and post-policy response content
6. **Pipeline instrumentation**: Hook to snapshot pre-policy response in test mode

## Frontend Changes Required

1. **Available panel**: Category accordion replacing Simple/Advanced split
2. **Proposed panel**: Single-policy-first layout; chain UI only appears when >1 policy
3. **Active panel**: Before/After test display replacing single-output view
4. **Credential UX**: Default to "Uses your Claude credential" with collapsed override
5. **`form_renderer.js`**: Style improvements (descriptions visible, better spacing)
