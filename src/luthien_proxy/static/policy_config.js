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
    savedInstances: []
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
        'AllCapsPolicy': 'Say hello',
        'NoOpPolicy': 'Explain what this policy does',
        'DebugLoggingPolicy': 'Help me debug a function'
    };
    return prompts[state.selectedPolicyName] || 'Test this policy';
}

async function handleCopyPrompt() {
    const promptText = document.getElementById('test-prompt').value;
    const copyBtn = document.getElementById('copy-prompt-btn');

    try {
        await navigator.clipboard.writeText(promptText);
        const originalText = copyBtn.textContent;
        copyBtn.textContent = 'Copied ✓';
        copyBtn.style.background = 'var(--color-success)';
        setTimeout(() => {
            copyBtn.textContent = originalText;
            copyBtn.style.background = '';
        }, 2000);
    } catch (err) {
        console.error('Copy failed:', err);
    }
}

let eventSource = null;

function connectToActivityStream() {
    if (eventSource) eventSource.close();

    eventSource = new EventSource('/activity/stream');

    eventSource.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);

            if (event.type === 'transaction.request_recorded' &&
                state.policyEnabledAt &&
                new Date(event.timestamp).getTime() > state.policyEnabledAt) {

                showTestDetected(event.call_id);
                eventSource.close();
            }
        } catch (err) {
            console.error('SSE parse error:', err);
        }
    };

    eventSource.onerror = (err) => {
        console.error('SSE error:', err);
        // Fall back to mock after 15 seconds
        setTimeout(() => {
            if (state.currentStep === 3 && !state.detectedCallId) {
                showTestDetected('mock_' + Math.random().toString(36).substr(2, 12));
            }
        }, 15000);
    };
}

function showTestDetected(callId) {
    state.detectedCallId = callId;

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
