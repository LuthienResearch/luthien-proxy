# Policy Configuration UI - Specification

**Version:** 1.0
**Date:** 2025-11-11
**Status:** Ready for Development

---

## Overview

A standalone web page that allows Luthien users to enable, test, and configure policies through a guided wizard interface. This prototype focuses on front-end UX with mocked backend behavior to define API requirements.

---

## User Flow

### Current State (Manual Process)
1. User opens `config/v2_config.yaml` in editor
2. User uncomments desired policy
3. User runs `docker compose restart v2-gateway` in terminal
4. User waits for restart
5. User tests policy in Claude Code terminal
6. User manually checks results

### New Flow (Policy Configuration UI)
1. User opens Policy Configuration UI in browser
2. User browses and selects a policy from visual list
3. User clicks "Enable & Test" button
4. User sees real-time status updates (restarting → enabled)
5. User copies suggested test prompt
6. Page automatically detects test completion
7. User navigates to Activity Monitor or Diff Viewer to see results

---

## Technical Specifications

### File Structure
```
src/luthien_proxy/static/
├── activity_monitor.html      (existing)
├── activity_monitor.js         (existing)
├── diff_viewer.html           (existing)
├── policy_config.html         (NEW - single file with embedded CSS & JS)
└── policy_config.js           (NEW - JavaScript for policy config UI)
```

### Route
- **URL:** `/policy-config`
- **Method:** GET
- **Returns:** `policy_config.html`

### Technology Stack
- **HTML5** - Single page structure
- **CSS3** - Embedded in `<style>` tag, following existing dark theme design system
- **Vanilla JavaScript** - Embedded in `<script>` tag
- **No dependencies** - Self-contained file
- **Mock backend** - Simulated delays and responses (no actual API calls in v1)

---

## Page Structure

### Layout Components

#### 1. Navigation Header
```
┌─────────────────────────────────────────────────────────────┐
│ Luthien Proxy                                               │
│ [Activity Monitor] [Policy Config ●] [Diff Viewer]          │
└─────────────────────────────────────────────────────────────┘
```
- Dark theme header matching Activity Monitor
- Three navigation links: `/activity/monitor`, `/policy-config` (current/active), `/debug/diff`
- Current page indicated with bullet or highlight

#### 2. Page Title & Subtitle
```
Policy Configuration
Live configuration and testing of Luthien policies
```

#### 3. Wizard Stepper
```
 (1)─────────────(2)─────────────(3)
Select Policy   Enable & Test   Try out Policy
  [filled]        [outline]       [outline]
```
- Numbered circles (1, 2, 3) with connecting lines
- Circle states: filled (current), outline (future), checkmark (completed)
- Users can click previous steps to go back
- Line between circles: solid (completed), dashed (upcoming)

#### 4. Step Content Area
- Main content area showing current step
- Previous steps remain visible but collapsed/dimmed
- Next steps hidden until unlocked

---

## Step 1: Select Policy

### Layout: Vertical List with Full-Width Cards

```
┌────────────────────────────────────────────────────────────┐
│ All Caps Policy                                            │
├────────────────────────────────────────────────────────────┤
│ Converts all LLM responses to uppercase                    │
│                                                            │
│ Example: Input: 'Hello' → Output: 'HELLO'                 │
│                                                            │
│ Use case: Good for testing basic policy functionality     │
│                                                            │
│                                     [Select Policy] →      │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ Block Tool Calls Policy                                    │
├────────────────────────────────────────────────────────────┤
│ Prevents execution of specified tools (execute_code, Bash) │
│                                                            │
│ Example: Input: 'Run ls command' → Output: [Tool blocked] │
│                                                            │
│ Use case: Prevent code execution in sensitive contexts    │
│                                                            │
│                                     [Select Policy] →      │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ Natural Language Policy                                    │
├────────────────────────────────────────────────────────────┤
│ Enforces custom rules defined in natural language          │
│                                                            │
│ Example: "Don't cheat on unit tests" → Policy enforced    │
│                                                            │
│ Use case: Domain-specific safety constraints              │
│                                                            │
│                                     [Select Policy] →      │
└────────────────────────────────────────────────────────────┘
```

### Card Styling
- Background: `#141414` (--bg-secondary)
- Border: `1px solid #262626`
- Border-radius: `8px`
- Padding: `24px`
- Margin-bottom: `16px`

### Card Content
Each policy card displays:
1. **Policy Name** (H3, 18px, bold, white)
2. **One-line description** (14px, #e5e5e5)
3. **Example** (13px, #888, monospace for code parts)
4. **Use case** (13px, #888, italic)
5. **Select button** (right-aligned, primary style)

### Interaction
- Clicking "Select Policy" button:
  - Highlights selected card (green border)
  - Updates stepper (Step 1 → checkmark, Step 2 → filled)
  - Scrolls to/reveals Step 2
  - Stores selected policy in JavaScript state

---

## Step 2: Enable & Test

### Initial View (Before Button Click)

```
┌────────────────────────────────────────────────────────────┐
│ You selected: All Caps Policy                             │
│ Converts all LLM responses to uppercase                    │
│                                                            │
│ What happens next:                                         │
│ • Gateway will restart with this policy (Takes ~5-10 sec)  │
│                                                            │
│                   [Enable & Test]                          │
└────────────────────────────────────────────────────────────┘
```

### During Restart (After Button Click)

```
┌────────────────────────────────────────────────────────────┐
│ You selected: All Caps Policy                             │
│ Converts all LLM responses to uppercase                    │
│                                                            │
│ Status:                                                    │
│ ⏳ Restarting gateway...                                   │
│                                                            │
│                   [Enable & Test] (disabled)               │
└────────────────────────────────────────────────────────────┘
```

After 3 seconds:

```
│ Status:                                                    │
│ ✓ Successfully restarted                                   │
│ ⏳ Enabling All Caps Policy...                             │
```

After 5 seconds total:

```
│ Status:                                                    │
│ ✓ Successfully restarted                                   │
│ ✓ All Caps Policy enabled                                  │
│                                                            │
│                   [Continue to Testing →]                  │
```

### Status Message Styling
- Container: `#1e3a5f` background (info box)
- Border: `1px solid #2563eb`
- Padding: `16px`
- Messages: 14px, line-height 1.8
- Loading icon (⏳): Spinning animation
- Success icon (✓): Green `#4ade80`
- Error icon (❌): Red `#f87171`

### Button States
- **Default:** Primary button (blue background)
- **Disabled:** Grayed out, cursor not-allowed
- **After success:** Becomes "Continue to Testing" (green accent)

### Error Handling

If restart fails:

```
│ Status:                                                    │
│ ❌ Error: Failed to restart gateway                        │
│                                                            │
│ [Error message from backend displayed here]               │
│                                                            │
│ Troubleshooting steps:                                     │
│ • [Dynamic step 1 from backend]                            │
│ • [Dynamic step 2 from backend]                            │
│ • [Dynamic step 3 from backend]                            │
│                                                            │
│                   [Retry]  [Back to Step 1]                │
```

**Error Display Design:**
- Background: `#4d1a1a` (dark red)
- Border: `1px solid #f87171` (red)
- Text color: `#fca5a5` (light red)
- Troubleshooting list: `#e5e5e5` text

**Generic Troubleshooting (for mock):**
- Check that Docker is running
- Verify gateway is accessible at localhost:8000
- Try restarting manually with: `docker compose restart v2-gateway`

**Note for backend:** API should return structured errors with dynamic troubleshooting steps specific to failure type.

### JavaScript Behavior (Mocked)

```javascript
async function handleEnablePolicy(policyName) {
    // Disable button
    enableButton.disabled = true;
    enableButton.textContent = 'Enabling...';

    // Show status container
    statusContainer.innerHTML = '<p>⏳ Restarting gateway...</p>';

    // Mock delay: 3 seconds
    await sleep(3000);

    // Update status
    statusContainer.innerHTML = `
        <p>✓ Successfully restarted</p>
        <p>⏳ Enabling ${policyName}...</p>
    `;

    // Mock delay: 2 more seconds
    await sleep(2000);

    // Final status
    statusContainer.innerHTML = `
        <p>✓ Successfully restarted</p>
        <p>✓ ${policyName} enabled</p>
    `;

    // Show continue button
    enableButton.textContent = 'Continue to Testing →';
    enableButton.disabled = false;
    enableButton.onclick = () => goToStep(3);

    // Update stepper: Step 2 complete, Step 3 active
    updateStepper(3);
}
```

---

## Step 3: Try out Policy

### Initial View

```
┌────────────────────────────────────────────────────────────┐
│ Test the All Caps Policy                                   │
├────────────────────────────────────────────────────────────┤
│ Copy this test prompt and paste it into your Claude Code   │
│ terminal:                                                  │
│                                                            │
│ ┌────────────────────────────────────────────────────┐    │
│ │ Explain when it's appropriate to use ALL CAPS in  │    │
│ │ written communication.                            │    │
│ └────────────────────────────────────────────────────┘    │
│                                         [Copy Prompt]      │
│                                                            │
│ ⏳ Waiting for test request...                             │
└────────────────────────────────────────────────────────────┘
```

### After Detection (SSE detects test)

```
┌────────────────────────────────────────────────────────────┐
│ Test the All Caps Policy                                   │
├────────────────────────────────────────────────────────────┤
│ ✓ Test detected! Policy was applied to your request       │
│                                                            │
│ Call ID: call_abc123xyz456                                 │
│                                                            │
│ View your results:                                         │
│ • [View in Activity Monitor] →                             │
│ • [View Diff for call_abc123xyz456] →                      │
└────────────────────────────────────────────────────────────┘
```

### Test Prompts by Policy

#### All Caps Policy
```
Explain when it's appropriate to use ALL CAPS in written communication.
```

#### Block Tool Calls Policy
```
Please list all files in my current directory using the Bash tool.
(This should be blocked by the policy)
```

#### Natural Language Policy
```
Help me write a unit test that always passes without actually
testing the functionality. (This violates the "don't cheat on
unit tests" policy)
```

### Real-Time Detection (SSE Integration)

**JavaScript Implementation:**

```javascript
// Connect to activity stream
const eventSource = new EventSource('/activity/stream');

// Track policy enable timestamp
let policyEnabledAt = Date.now();

eventSource.onmessage = (e) => {
    const event = JSON.parse(e.data);

    // Check if this is a new request after policy was enabled
    if (event.type === 'transaction.request_recorded' &&
        event.timestamp > policyEnabledAt) {

        // Show success state
        showTestDetected(event.call_id);
    }
};

function showTestDetected(callId) {
    testStatusDiv.innerHTML = `
        <p>✓ Test detected! Policy was applied to your request</p>
        <p>Call ID: ${callId}</p>
        <br>
        <p>View your results:</p>
        <a href="/activity/monitor">View in Activity Monitor →</a>
        <a href="/debug/diff?call_id=${callId}">View Diff for ${callId} →</a>
    `;
}
```

**Mock Behavior (for v1 without SSE):**

```javascript
// Simulate detection after 10 seconds
setTimeout(() => {
    const mockCallId = 'call_' + Math.random().toString(36).substr(2, 9);
    showTestDetected(mockCallId);
}, 10000);
```

### Copy Button Functionality

```javascript
copyButton.addEventListener('click', async () => {
    const prompt = testPromptTextarea.value;
    await navigator.clipboard.writeText(prompt);

    // Show feedback
    copyButton.textContent = 'Copied ✓';
    setTimeout(() => {
        copyButton.textContent = 'Copy Prompt';
    }, 2000);
});
```

---

## Styling & Design System

### Color Palette (Dark Theme)

```css
:root {
    /* Backgrounds */
    --bg-primary: #0a0a0a;
    --bg-secondary: #141414;
    --bg-tertiary: #1a1a1a;

    /* Text */
    --text-primary: #e5e5e5;
    --text-secondary: #888;
    --text-tertiary: #666;

    /* Borders */
    --border-color: #262626;
    --border-light: #333;

    /* Status Colors */
    --color-success: #4ade80;
    --color-error: #f87171;
    --color-warning: #f59e0b;
    --color-info: #2563eb;
    --color-neutral: #06b6d4;

    /* Spacing */
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 12px;
    --spacing-lg: 16px;
    --spacing-xl: 24px;
    --spacing-2xl: 32px;

    /* Border Radius */
    --radius-sm: 4px;
    --radius-md: 6px;
    --radius-lg: 8px;
}
```

### Typography

```css
/* System font stack */
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    line-height: 1.6;
    color: var(--text-primary);
}

h1 { font-size: 24px; font-weight: 600; }
h2 { font-size: 20px; font-weight: 600; }
h3 { font-size: 18px; font-weight: 600; }

/* Monospace for code */
.monospace {
    font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
    font-size: 12px;
}
```

### Component Styles

#### Button
```css
button {
    padding: 8px 16px;
    background: var(--bg-secondary);
    border: 1px solid var(--border-light);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-size: 13px;
    cursor: pointer;
    transition: all 0.2s;
}

button:hover {
    border-color: var(--color-info);
    background: var(--bg-tertiary);
}

button.primary {
    background: var(--color-info);
    border-color: var(--color-info);
    color: white;
}

button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
```

#### Card
```css
.card {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: var(--spacing-xl);
    margin-bottom: var(--spacing-lg);
}

.card.selected {
    border-color: var(--color-success);
    border-width: 2px;
}
```

#### Status Box
```css
.status-box {
    background: #1e3a5f;
    border: 1px solid var(--color-info);
    border-radius: var(--radius-md);
    padding: var(--spacing-lg);
    color: #60a5fa;
}

.status-box.error {
    background: #4d1a1a;
    border-color: var(--color-error);
    color: #fca5a5;
}

.status-box.success {
    background: #0a3d0a;
    border-color: var(--color-success);
    color: var(--color-success);
}
```

#### Stepper
```css
.stepper {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: var(--spacing-2xl) 0;
}

.step {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--spacing-sm);
}

.step-circle {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    border: 2px solid var(--border-light);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 600;
    transition: all 0.3s;
}

.step-circle.active {
    background: var(--color-info);
    border-color: var(--color-info);
    color: white;
}

.step-circle.completed {
    background: var(--color-success);
    border-color: var(--color-success);
    color: white;
}

.step-line {
    flex: 1;
    height: 2px;
    background: var(--border-light);
    margin: 0 var(--spacing-md);
}

.step-line.completed {
    background: var(--color-success);
}
```

---

## Backend API Requirements (To Be Implemented)

### Endpoint: Enable Policy

**Request:**
```http
POST /policy/enable
Content-Type: application/json

{
  "policy": "AllCapsPolicy",
  "config": {}
}
```

**Success Response:**
```json
{
  "status": "success",
  "message": "Policy enabled successfully",
  "policy": "AllCapsPolicy",
  "restart_duration_ms": 4823
}
```

**Error Response:**
```json
{
  "status": "error",
  "error": "Failed to restart gateway",
  "details": "Docker container not responding",
  "troubleshooting": [
    "Check that Docker is running",
    "Verify gateway is accessible at localhost:8000",
    "Try restarting manually: docker compose restart v2-gateway"
  ]
}
```

### Endpoint: Get Available Policies

**Request:**
```http
GET /policy/list
```

**Response:**
```json
{
  "policies": [
    {
      "id": "AllCapsPolicy",
      "name": "All Caps Policy",
      "description": "Converts all LLM responses to uppercase",
      "example": {
        "input": "Hello",
        "output": "HELLO"
      },
      "use_case": "Good for testing basic policy functionality",
      "config_schema": {}
    },
    // ... more policies
  ]
}
```

### Endpoint: Get Current Policy

**Request:**
```http
GET /policy/current
```

**Response:**
```json
{
  "policy": "AllCapsPolicy",
  "enabled_at": "2025-11-11T10:30:00Z",
  "config": {}
}
```

---

## SSE Event Format (For Step 3 Detection)

The page should listen to existing `/activity/stream` endpoint:

**Event to detect:**
```json
{
  "type": "transaction.request_recorded",
  "timestamp": "2025-11-11T10:35:22.123Z",
  "call_id": "call_abc123xyz",
  "transaction_id": "tx_456def",
  "payload": {
    // ... request details
  }
}
```

**Detection Logic:**
- Track timestamp when policy was enabled
- Listen for `transaction.request_recorded` events
- Match events with timestamp > policy_enabled_timestamp
- Extract `call_id` from first matching event
- Display success state with call_id

---

## Development Checklist

### Phase 1: Static Layout (No JavaScript)
- [ ] Create `policy_config.html` file
- [ ] Implement navigation header
- [ ] Implement page title and subtitle
- [ ] Implement stepper UI (static circles and lines)
- [ ] Implement Step 1 with 3 policy cards
- [ ] Implement Step 2 layout
- [ ] Implement Step 3 layout
- [ ] Apply dark theme CSS
- [ ] Test responsive layout

### Phase 2: Step Navigation (Basic JavaScript)
- [ ] Implement step state management
- [ ] Implement stepper click navigation (back only)
- [ ] Implement "Select Policy" button → go to Step 2
- [ ] Implement card selection highlighting
- [ ] Implement "Continue to Testing" button → go to Step 3
- [ ] Test forward/backward navigation

### Phase 3: Mock Backend Interaction
- [ ] Implement "Enable & Test" button with mock delay
- [ ] Implement sequential status messages (restarting → enabled)
- [ ] Implement button state changes (disabled, loading, enabled)
- [ ] Test 5-second delay sequence
- [ ] Test multiple policy selections

### Phase 4: Copy Functionality
- [ ] Implement "Copy Prompt" button
- [ ] Implement clipboard API
- [ ] Implement visual feedback (button text change)
- [ ] Test on different browsers

### Phase 5: Mock Test Detection
- [ ] Implement 10-second setTimeout mock detection
- [ ] Generate mock call_id
- [ ] Display success state with call_id
- [ ] Create links to Activity Monitor and Diff Viewer
- [ ] Test call_id parameter passing to diff viewer

### Phase 6: Error Handling
- [ ] Create mock error state toggle (for testing)
- [ ] Implement error display UI
- [ ] Implement "Retry" button
- [ ] Implement "Back to Step 1" button
- [ ] Test error → retry flow

### Phase 7: Polish & Testing
- [ ] Add loading animations (spinner, pulse)
- [ ] Add smooth transitions between states
- [ ] Test all three policy selections
- [ ] Test navigation between all steps
- [ ] Cross-browser testing (Chrome, Firefox, Safari)
- [ ] Mobile responsive check
- [ ] Accessibility check (keyboard navigation, screen reader)

### Phase 8: Integration Preparation
- [ ] Document all mock delay values
- [ ] Document expected API endpoints
- [ ] Document expected API request/response formats
- [ ] Create backend integration guide
- [ ] Add code comments for future SSE integration

---

## Future Enhancements (Post-v1)

### Backend Integration
- Replace mock delays with real API calls
- Implement SSE connection for real-time test detection
- Add policy configuration validation
- Persist user's last selected policy

### Enhanced Error Handling
- Specific error types with tailored troubleshooting
- Error logging and reporting
- Health check integration

### Additional Features
- Policy configuration form (for policies with config options)
- Policy comparison view (compare two policies side-by-side)
- Policy history/audit log
- Pre-configured policy templates
- Custom policy creation wizard

### UX Improvements
- Keyboard shortcuts (n=next, p=previous, etc.)
- Tooltips with more policy details
- Video tutorials embedded in UI
- Onboarding tour for first-time users
- Export test results as PDF/JSON

---

## Testing Scenarios

### Happy Path
1. Open `/policy-config`
2. See 3 policy cards in Step 1
3. Click "Select Policy" on All Caps Policy
4. See Step 2 with confirmation
5. Click "Enable & Test"
6. Wait 5 seconds, see status messages
7. Click "Continue to Testing"
8. See test prompt with Copy button
9. Click "Copy Prompt"
10. Wait 10 seconds (mock detection)
11. See success message with call_id
12. Click "View in Activity Monitor"
13. Verify navigation to Activity Monitor

### Error Path
1. Complete steps 1-5 from Happy Path
2. (Simulate error during mock delay)
3. See error message with troubleshooting steps
4. Click "Retry"
5. See status reset
6. Complete successfully

### Back Navigation
1. Complete steps 1-4 from Happy Path
2. Click "Step 1" in stepper
3. See Step 1 again, previous selection still highlighted
4. Select different policy
5. Click "Step 2" in stepper (should not work - future step)
6. Complete flow

---

## Success Criteria

### Functional Requirements
✅ User can browse 3 policies with full details
✅ User can select a policy and advance to Step 2
✅ User can click "Enable & Test" and see status updates
✅ User can navigate back to previous steps
✅ User can copy test prompt to clipboard
✅ User receives success confirmation after mock test
✅ User can navigate to Activity Monitor and Diff Viewer
✅ Error state displays with troubleshooting steps

### Design Requirements
✅ Matches existing dark theme design system
✅ Responsive layout works on desktop
✅ Smooth transitions and animations
✅ Clear visual hierarchy
✅ Accessible (keyboard navigation, ARIA labels)

### Technical Requirements
✅ Single HTML file with embedded CSS and JavaScript
✅ No external dependencies
✅ Mock delays simulate real backend behavior
✅ Code is well-commented for future integration
✅ Works in Chrome, Firefox, Safari

---

## Handoff Notes for Developer

### Key Implementation Notes

1. **State Management:** Use a simple JavaScript object to track:
   - Current step (1, 2, or 3)
   - Selected policy
   - Policy enabled timestamp
   - Detected call_id

2. **Mock Delays:**
   - Enable button click → 3s delay → "Successfully restarted"
   - → 2s delay → "Policy enabled"
   - Copy prompt → 10s delay → "Test detected"

3. **SSE Integration (Future):**
   - EventSource connection already used in Activity Monitor
   - Reuse same pattern in Step 3
   - Filter for `transaction.request_recorded` events
   - Match timestamp > policy_enabled_at

4. **Diff Viewer Link:**
   - Current diff viewer expects `call_id` as query param
   - May need to check actual implementation in `diff_viewer.html`
   - Link format: `/debug/diff?call_id=call_abc123`

5. **Policy Details:**
   - All 3 policies are hardcoded in HTML for v1
   - Future: Fetch from `/policy/list` endpoint
   - Policy config schema not implemented in v1 (all empty config: {})

6. **Route Setup:**
   - Add route in `src/luthien_proxy/ui/routes.py`:
     ```python
     @router.get("/policy-config")
     async def policy_config():
         return FileResponse("src/luthien_proxy/static/policy_config.html")
     ```

---

## Appendix: Complete File Template

See separate file: `policy_config_template.html` (to be created)

Structure:
```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Policy Configuration - Luthien Proxy</title>
    <style>
        /* Full CSS implementation */
    </style>
</head>
<body>
    <!-- Navigation header -->
    <!-- Page title -->
    <!-- Stepper -->
    <!-- Step 1 content -->
    <!-- Step 2 content -->
    <!-- Step 3 content -->

    <script>
        /* Full JavaScript implementation */
    </script>
</body>
</html>
```

---

**End of Specification**
