// Configuration constants
const TIMEOUTS = {
    // Policy activation
    CREATE_INSTANCE: 2000,      // 2s - instance creation time
    ACTIVATE_POLICY: 1000,      // 1s - policy activation time

    // SSE detection
    SSE_BASE_TIMEOUT: 5000,     // 5s - minimum wait time
    SSE_MAX_TIMEOUT: 30000,     // 30s - maximum wait time
    SSE_USER_ACTION_BUFFER: 6000, // 6s - time for user to copy, switch, paste

    // UI feedback
    COPY_FEEDBACK: 2000,        // 2s - "Copied ✓" display time
};

// Estimated response times by policy type (in milliseconds)
const POLICY_RESPONSE_ESTIMATES = {
    'AllCapsPolicy': 3000,           // Fast - simple transformation
    'NoOpPolicy': 2000,              // Fastest - passthrough
    'DebugLoggingPolicy': 3000,      // Fast - logging only
    'BlockToolCallsPolicy': 2000,    // Fast - early termination
    'NaturalLanguagePolicy': 8000,   // Slower - LLM judgment needed
    'default': 5000                  // Default for unknown policies
};

// State
const state = {
    currentStep: 1,
    selectedPolicyClass: null,
    selectedPolicyName: null,
    instanceName: null,
    adminKey: null,
    policyEnabledAt: null,
    detectedCallId: null,
    availablePolicies: [],
    savedInstances: [],
    sseErrorFallbackTimer: null  // Track fallback timer for cleanup
};

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    checkAdminKey();
    await loadPolicies();
    await loadInstances();
    setupEventListeners();
});

function checkAdminKey() {
    state.adminKey = sessionStorage.getItem('admin_key');
    if (!state.adminKey) {
        state.adminKey = prompt('Enter admin API key:');
        if (state.adminKey) {
            sessionStorage.setItem('admin_key', state.adminKey);
        }
    }
}

async function apiCall(endpoint, options = {}) {
    const headers = {
        'Authorization': `Bearer ${state.adminKey}`,
        'Content-Type': 'application/json',
        ...options.headers
    };

    const response = await fetch(endpoint, { ...options, headers });
    if (response.status === 403) {
        sessionStorage.removeItem('admin_key');
        alert('Invalid admin key');
        checkAdminKey();
        throw new Error('Invalid admin key');
    }
    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Request failed');
    }
    return response.json();
}

async function loadPolicies() {
    try {
        const data = await apiCall('/admin/policy/list');
        state.availablePolicies = data.policies;
        renderPolicyCards();
    } catch (err) {
        console.error('Failed to load policies:', err);
        document.getElementById('step-1').innerHTML = `<p class="error">Failed to load policies: ${err.message}</p>`;
    }
}

async function loadInstances() {
    try {
        const data = await apiCall('/admin/policy/instances');
        state.savedInstances = data.instances;
    } catch (err) {
        console.error('Failed to load instances:', err);
    }
}

function renderPolicyCards() {
    const container = document.querySelector('.policy-grid');
    container.innerHTML = state.availablePolicies.map(policy => `
        <div class="policy-card" data-policy="${policy.class_ref}">
            <h3>${policy.name}</h3>
            <p>${policy.description}</p>
            <button class="select-policy-btn">Select</button>
        </div>
    `).join('');

    document.querySelectorAll('.select-policy-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const card = e.target.closest('.policy-card');
            selectPolicy(card.dataset.policy);
        });
    });
}

function setupEventListeners() {
    document.getElementById('create-activate-btn')?.addEventListener('click', handleCreateActivate);
    document.getElementById('copy-prompt-btn')?.addEventListener('click', handleCopyPrompt);

    // Allow clicking previous steps to go back
    document.querySelectorAll('.step').forEach(step => {
        step.addEventListener('click', () => {
            const stepNum = parseInt(step.dataset.step);
            if (stepNum < state.currentStep) {
                goToStep(stepNum);
            }
        });
    });
}

function selectPolicy(classRef) {
    const policy = state.availablePolicies.find(p => p.class_ref === classRef);
    if (!policy) return;

    state.selectedPolicyClass = policy;
    state.selectedPolicyName = policy.name;

    document.querySelectorAll('.policy-card').forEach(c => c.classList.remove('selected'));
    document.querySelector(`[data-policy="${classRef}"]`).classList.add('selected');

    document.getElementById('selected-policy-name').textContent = policy.name;
    document.getElementById('selected-policy-description').textContent = policy.description;
    document.getElementById('status-container').innerHTML = '';

    const btn = document.getElementById('create-activate-btn');
    btn.textContent = 'Create & Activate';
    btn.disabled = false;
    btn.className = 'primary';

    goToStep(2);
}

function goToStep(stepNum) {
    state.currentStep = stepNum;

    // Clean up any pending timers when leaving Step 3
    if (state.currentStep !== 3 && state.sseErrorFallbackTimer) {
        clearTimeout(state.sseErrorFallbackTimer);
        state.sseErrorFallbackTimer = null;
    }

    for (let i = 1; i <= 3; i++) {
        const stepEl = document.getElementById(`step-indicator-${i}`);
        const lineEl = document.getElementById(`line-${i}`);

        stepEl.classList.remove('active', 'completed', 'disabled');

        if (i < stepNum) {
            stepEl.classList.add('completed');
            if (lineEl) lineEl.classList.add('completed');
        } else if (i === stepNum) {
            stepEl.classList.add('active');
            if (lineEl) lineEl.classList.remove('completed');
        } else {
            stepEl.classList.add('disabled');
            if (lineEl) lineEl.classList.remove('completed');
        }
    }

    document.querySelectorAll('.step-content').forEach(c => c.classList.remove('active'));
    document.getElementById(`step-${stepNum}`).classList.add('active');
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function handleCreateActivate() {
    const btn = document.getElementById('create-activate-btn');
    const statusContainer = document.getElementById('status-container');

    btn.disabled = true;
    btn.textContent = 'Creating...';

    try {
        // Generate instance name
        state.instanceName = `${state.selectedPolicyName.toLowerCase().replace(/\s+/g, '-')}-${Date.now()}`;

        statusContainer.innerHTML = `<div class="status-box"><p><span class="status-icon spinner">⏳</span> Creating policy instance...</p></div>`;

        // Create instance
        await apiCall('/admin/policy/create', {
            method: 'POST',
            body: JSON.stringify({
                name: state.instanceName,
                policy_class_ref: state.selectedPolicyClass.class_ref,
                config: state.selectedPolicyClass.example_config || {},
                description: `Created from UI at ${new Date().toISOString()}`
            })
        });

        statusContainer.innerHTML = `<div class="status-box"><p><span class="status-icon">✓</span> Instance created</p><p><span class="status-icon spinner">⏳</span> Activating...</p></div>`;

        // Activate instance
        await apiCall('/admin/policy/activate', {
            method: 'POST',
            body: JSON.stringify({
                name: state.instanceName,
                activated_by: 'ui'
            })
        });

        statusContainer.innerHTML = `<div class="status-box success"><p><span class="status-icon">✓</span> ${state.selectedPolicyName} activated!</p></div>`;

        btn.textContent = 'Continue to Testing →';
        btn.className = 'success';
        btn.onclick = () => goToStep3();
        btn.disabled = false;

        state.policyEnabledAt = Date.now();
    } catch (err) {
        statusContainer.innerHTML = `<div class="status-box error"><p>Error: ${err.message}</p></div>`;
        btn.textContent = 'Retry';
        btn.disabled = false;
    }
}

/**
 * Calculate smart timeout for SSE detection based on policy type
 * @returns {number} Timeout in milliseconds
 */
function getSSEDetectionTimeout() {
    // Start with base timeout
    let timeout = TIMEOUTS.SSE_BASE_TIMEOUT;

    // Add buffer for user actions (copy, switch windows, paste, send)
    timeout += TIMEOUTS.SSE_USER_ACTION_BUFFER;

    // Add expected LLM response time based on policy
    const policyResponseTime = POLICY_RESPONSE_ESTIMATES[state.selectedPolicyName]
        || POLICY_RESPONSE_ESTIMATES.default;
    timeout += policyResponseTime;

    // Cap at maximum to prevent infinite waiting
    return Math.min(timeout, TIMEOUTS.SSE_MAX_TIMEOUT);
}

function goToStep3() {
    document.getElementById('test-policy-name').textContent = state.selectedPolicyName;
    document.getElementById('test-prompt').value = getTestPrompt();

    const testStatus = document.getElementById('test-status');
    testStatus.className = 'test-status waiting';
    testStatus.innerHTML = `<p><span class="status-icon spinner">⏳</span> Waiting for test request...</p>`;

    goToStep(3);
    connectToActivityStream();
}

function getTestPrompt() {
    const prompts = {
        'AllCapsPolicy': "Explain when it's appropriate to use ALL CAPS in written communication.",
        'NoOpPolicy': 'Explain what this policy does and when to use it.',
        'DebugLoggingPolicy': 'Help me debug a simple JavaScript function.',
        'BlockToolCallsPolicy': 'Please list all files in my current directory using the Bash tool.',
        'NaturalLanguagePolicy': 'Help me write a unit test that always passes without testing functionality.'
    };
    return prompts[state.selectedPolicyName] || 'Test this policy with a sample prompt.';
}

async function handleCopyPrompt() {
    const promptText = document.getElementById('test-prompt').value;
    const copyBtn = document.getElementById('copy-prompt-btn');

    try {
        await navigator.clipboard.writeText(promptText);
        const originalText = copyBtn.textContent;
        copyBtn.textContent = 'Copied ✓';
        copyBtn.style.background = 'var(--color-success)';
        copyBtn.style.borderColor = 'var(--color-success)';
        copyBtn.style.color = 'white';

        setTimeout(() => {
            copyBtn.textContent = originalText;
            copyBtn.style.background = '';
            copyBtn.style.borderColor = '';
            copyBtn.style.color = '';
        }, TIMEOUTS.COPY_FEEDBACK);
    } catch (err) {
        console.error('Copy failed:', err);
        copyBtn.textContent = 'Copy failed';
        setTimeout(() => {
            copyBtn.textContent = 'Copy Prompt';
        }, TIMEOUTS.COPY_FEEDBACK);
    }
}

let eventSource = null;

function connectToActivityStream() {
    // Close existing connection if any
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    // Clear any existing fallback timer
    if (state.sseErrorFallbackTimer) {
        clearTimeout(state.sseErrorFallbackTimer);
        state.sseErrorFallbackTimer = null;
    }

    eventSource = new EventSource('/activity/stream');

    eventSource.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);

            // Check if this is a new request after policy was enabled
            if (event.type === 'transaction.request_recorded' &&
                state.policyEnabledAt &&
                new Date(event.timestamp).getTime() > state.policyEnabledAt) {

                showTestDetected(event.call_id);
                eventSource.close();
                eventSource = null;
            }
        } catch (err) {
            console.error('SSE parse error:', err);
        }
    };

    eventSource.onerror = (err) => {
        console.error('SSE connection error:', err);

        // Set up smart fallback timeout
        const detectionTimeout = getSSEDetectionTimeout();
        const timeoutSeconds = (detectionTimeout / 1000).toFixed(1);

        state.sseErrorFallbackTimer = setTimeout(() => {
            // Only show error if we're still on Step 3 and haven't detected anything
            if (state.currentStep === 3 && !state.detectedCallId) {
                const testStatus = document.getElementById('test-status');
                testStatus.className = 'test-status error';
                testStatus.innerHTML = `
                    <p><span class="status-icon">⚠️</span> Unable to auto-detect test completion (waited ${timeoutSeconds}s)</p>
                    <p>Your test may have completed successfully. Check the <a href="/activity/monitor">Activity Monitor</a> to verify.</p>

                    <details style="margin-top: 16px; color: var(--text-secondary); font-size: 12px;">
                        <summary style="cursor: pointer; user-select: none;">Troubleshooting</summary>
                        <ul style="margin-top: 8px; padding-left: 20px; line-height: 1.8;">
                            <li>Ensure you pasted and sent the test prompt in Claude Code/Codex</li>
                            <li>Verify your AI assistant is configured to use the proxy at <code>localhost:8000</code></li>
                            <li>Check Network tab in browser DevTools for <code>/activity/stream</code> connection</li>
                            <li>Expected timeout for ${state.selectedPolicyName}: ${timeoutSeconds}s</li>
                            <li>If the request completed, find it in <a href="/activity/monitor">Activity Monitor</a></li>
                        </ul>
                    </details>
                `;

                // Close SSE connection since we're showing error
                if (eventSource) {
                    eventSource.close();
                    eventSource = null;
                }
            }
        }, detectionTimeout);
    };
}

function showTestDetected(callId) {
    state.detectedCallId = callId;

    // Clear fallback timer since we successfully detected
    if (state.sseErrorFallbackTimer) {
        clearTimeout(state.sseErrorFallbackTimer);
        state.sseErrorFallbackTimer = null;
    }

    const testStatus = document.getElementById('test-status');
    testStatus.className = 'test-status detected';
    testStatus.innerHTML = `
        <p><span class="status-icon">✓</span> Test detected! Policy was applied</p>
        <p>Call ID: <span class="call-id">${callId}</span></p>
        <div class="result-links">
            <h4>View results:</h4>
            <a href="/activity/monitor">Activity Monitor</a>
            <a href="/debug/diff?call_id=${callId}">View Diff</a>
        </div>
    `;
}
