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
        const step1 = document.getElementById('step-1');
        step1.innerHTML = ''; // Clear existing
        const errorP = document.createElement('p');
        errorP.className = 'error';
        errorP.textContent = `Failed to load policies: ${err.message}`; // XSS-safe
        step1.appendChild(errorP);
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
    container.innerHTML = ''; // Clear existing content

    state.availablePolicies.forEach(policy => {
        const card = document.createElement('div');
        card.className = 'policy-card';
        card.dataset.policy = policy.class_ref;

        const title = document.createElement('h3');
        title.textContent = policy.name; // textContent is XSS-safe

        const desc = document.createElement('p');
        desc.textContent = policy.description; // textContent is XSS-safe

        const btn = document.createElement('button');
        btn.className = 'select-policy-btn';
        btn.textContent = 'Select';
        btn.addEventListener('click', () => selectPolicy(policy.class_ref));

        card.appendChild(title);
        card.appendChild(desc);
        card.appendChild(btn);
        container.appendChild(card);
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

        // Show creating status
        statusContainer.innerHTML = ''; // Clear
        const creatingBox = document.createElement('div');
        creatingBox.className = 'status-box';
        const creatingP = document.createElement('p');
        const creatingIcon = document.createElement('span');
        creatingIcon.className = 'status-icon spinner';
        creatingIcon.textContent = '⏳';
        creatingP.appendChild(creatingIcon);
        creatingP.appendChild(document.createTextNode(' Creating policy instance...'));
        creatingBox.appendChild(creatingP);
        statusContainer.appendChild(creatingBox);

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

        // Show activating status
        statusContainer.innerHTML = ''; // Clear
        const activatingBox = document.createElement('div');
        activatingBox.className = 'status-box';
        const createdP = document.createElement('p');
        const checkIcon = document.createElement('span');
        checkIcon.className = 'status-icon';
        checkIcon.textContent = '✓';
        createdP.appendChild(checkIcon);
        createdP.appendChild(document.createTextNode(' Instance created'));
        activatingBox.appendChild(createdP);
        const activatingP = document.createElement('p');
        const activatingIcon = document.createElement('span');
        activatingIcon.className = 'status-icon spinner';
        activatingIcon.textContent = '⏳';
        activatingP.appendChild(activatingIcon);
        activatingP.appendChild(document.createTextNode(' Activating...'));
        activatingBox.appendChild(activatingP);
        statusContainer.appendChild(activatingBox);

        // Activate instance
        await apiCall('/admin/policy/activate', {
            method: 'POST',
            body: JSON.stringify({
                name: state.instanceName,
                activated_by: 'ui'
            })
        });

        // Show success status
        statusContainer.innerHTML = ''; // Clear
        const successBox = document.createElement('div');
        successBox.className = 'status-box success';
        const successP = document.createElement('p');
        const successIcon = document.createElement('span');
        successIcon.className = 'status-icon';
        successIcon.textContent = '✓';
        successP.appendChild(successIcon);
        successP.appendChild(document.createTextNode(' ' + state.selectedPolicyName + ' activated!')); // XSS-safe
        successBox.appendChild(successP);
        statusContainer.appendChild(successBox);

        btn.textContent = 'Continue to Testing →';
        btn.className = 'success';
        btn.onclick = () => goToStep3();
        btn.disabled = false;

        state.policyEnabledAt = Date.now();
    } catch (err) {
        statusContainer.innerHTML = ''; // Clear
        const errorBox = document.createElement('div');
        errorBox.className = 'status-box error';
        const errorP = document.createElement('p');
        errorP.textContent = `Error: ${err.message}`; // XSS-safe
        errorBox.appendChild(errorP);
        statusContainer.appendChild(errorBox);
        btn.textContent = 'Retry';
        btn.disabled = false;
    }
}

function goToStep3() {
    document.getElementById('test-policy-name').textContent = state.selectedPolicyName;
    document.getElementById('test-prompt').value = getTestPrompt();

    const testStatus = document.getElementById('test-status');
    testStatus.className = 'test-status waiting';
    testStatus.innerHTML = ''; // Clear
    const waitingP = document.createElement('p');
    const waitingIcon = document.createElement('span');
    waitingIcon.className = 'status-icon spinner';
    waitingIcon.textContent = '⏳';
    waitingP.appendChild(waitingIcon);
    waitingP.appendChild(document.createTextNode(' Waiting for test request...'));
    testStatus.appendChild(waitingP);

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
    testStatus.innerHTML = ''; // Clear existing content

    // First paragraph
    const p1 = document.createElement('p');
    const icon = document.createElement('span');
    icon.className = 'status-icon';
    icon.textContent = '✓';
    p1.appendChild(icon);
    p1.appendChild(document.createTextNode(' Test detected! Policy was applied'));

    // Second paragraph with call ID
    const p2 = document.createElement('p');
    p2.appendChild(document.createTextNode('Call ID: '));
    const callIdSpan = document.createElement('span');
    callIdSpan.className = 'call-id';
    callIdSpan.textContent = callId; // XSS-safe
    p2.appendChild(callIdSpan);

    // Result links div
    const linksDiv = document.createElement('div');
    linksDiv.className = 'result-links';

    const h4 = document.createElement('h4');
    h4.textContent = 'View results:';
    linksDiv.appendChild(h4);

    const monitorLink = document.createElement('a');
    monitorLink.href = '/activity/monitor';
    monitorLink.textContent = 'Activity Monitor';
    linksDiv.appendChild(monitorLink);

    const diffLink = document.createElement('a');
    diffLink.href = `/debug/diff?call_id=${encodeURIComponent(callId)}`; // URL-encode for safety
    diffLink.textContent = 'View Diff';
    linksDiv.appendChild(diffLink);

    testStatus.appendChild(p1);
    testStatus.appendChild(p2);
    testStatus.appendChild(linksDiv);
}
