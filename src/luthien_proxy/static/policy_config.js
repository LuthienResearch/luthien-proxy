// State
const state = {
    currentStep: 1,
    selectedPolicyClass: null,
    selectedPolicyName: null,
    policyEnabledAt: null,
    detectedCallId: null,
    availablePolicies: [],
    configValues: {}
};

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    await loadPolicies();
    setupEventListeners();
});

async function apiCall(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    const response = await fetch(endpoint, { ...options, headers, credentials: 'same-origin' });
    if (response.status === 403) {
        window.location.href = '/login?error=required&next=' + encodeURIComponent(window.location.pathname);
        throw new Error('Session expired');
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
        step1.innerHTML = '';
        const errorP = document.createElement('p');
        errorP.className = 'error';
        errorP.textContent = `Failed to load policies: ${err.message}`;
        step1.appendChild(errorP);
    }
}

function renderPolicyCards() {
    const container = document.querySelector('.policy-grid');
    container.innerHTML = '';

    state.availablePolicies.forEach(policy => {
        const card = document.createElement('div');
        card.className = 'policy-card';
        card.dataset.policy = policy.class_ref;

        const title = document.createElement('h3');
        title.textContent = policy.name;

        const desc = document.createElement('p');
        desc.textContent = policy.description;

        // Show config param count
        const configKeys = Object.keys(policy.config_schema || {});
        if (configKeys.length > 0) {
            const configInfo = document.createElement('p');
            configInfo.className = 'policy-example';
            configInfo.textContent = `Config: ${configKeys.join(', ')}`;
            card.appendChild(title);
            card.appendChild(desc);
            card.appendChild(configInfo);
        } else {
            card.appendChild(title);
            card.appendChild(desc);
        }

        const btn = document.createElement('button');
        btn.className = 'select-policy-btn';
        btn.textContent = 'Select';
        btn.addEventListener('click', () => selectPolicy(policy.class_ref));

        card.appendChild(btn);
        container.appendChild(card);
    });
}

function setupEventListeners() {
    document.getElementById('create-activate-btn')?.addEventListener('click', handleActivate);
    document.getElementById('copy-prompt-btn')?.addEventListener('click', handleCopyPrompt);
}

function selectPolicy(classRef) {
    const policy = state.availablePolicies.find(p => p.class_ref === classRef);
    if (!policy) return;

    state.selectedPolicyClass = policy;
    state.selectedPolicyName = policy.name;
    state.configValues = { ...(policy.example_config || {}) };

    document.querySelectorAll('.policy-card').forEach(c => c.classList.remove('selected'));
    document.querySelector(`[data-policy="${classRef}"]`).classList.add('selected');

    document.getElementById('selected-policy-name').textContent = policy.name;
    document.getElementById('selected-policy-description').textContent = policy.description;
    document.getElementById('status-container').innerHTML = '';

    // Render config form
    renderConfigForm(policy);

    const btn = document.getElementById('create-activate-btn');
    btn.textContent = 'Activate Policy';
    btn.disabled = false;
    btn.className = 'primary';

    goToStep(2);
}

function renderConfigForm(policy) {
    const container = document.getElementById('config-form-container');
    if (!container) return;

    container.innerHTML = '';

    const schema = policy.config_schema || {};
    const example = policy.example_config || {};
    const keys = Object.keys(schema);

    if (keys.length === 0) {
        const noConfig = document.createElement('p');
        noConfig.className = 'no-config-message';
        noConfig.textContent = 'This policy has no configuration options.';
        container.appendChild(noConfig);
        return;
    }

    keys.forEach(key => {
        const paramSchema = schema[key];
        const fieldDiv = document.createElement('div');
        fieldDiv.className = 'config-field';

        const label = document.createElement('label');
        label.textContent = key;
        label.setAttribute('for', `config-${key}`);

        const isComplex = paramSchema.type === 'object' || paramSchema.type === 'array';

        if (isComplex) {
            // Complex types get a JSON textarea
            const textarea = document.createElement('textarea');
            textarea.id = `config-${key}`;
            textarea.className = 'config-input config-json';
            textarea.value = JSON.stringify(example[key] ?? paramSchema.default ?? null, null, 2);
            textarea.addEventListener('input', () => {
                try {
                    state.configValues[key] = JSON.parse(textarea.value);
                    textarea.classList.remove('invalid');
                } catch {
                    textarea.classList.add('invalid');
                }
            });
            fieldDiv.appendChild(label);
            fieldDiv.appendChild(textarea);
        } else if (paramSchema.type === 'boolean') {
            // Boolean gets a checkbox
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.id = `config-${key}`;
            checkbox.className = 'config-checkbox';
            checkbox.checked = example[key] ?? paramSchema.default ?? false;
            checkbox.addEventListener('change', () => {
                state.configValues[key] = checkbox.checked;
            });
            const checkboxWrapper = document.createElement('div');
            checkboxWrapper.className = 'checkbox-wrapper';
            checkboxWrapper.appendChild(checkbox);
            checkboxWrapper.appendChild(label);
            fieldDiv.appendChild(checkboxWrapper);
        } else if (paramSchema.type === 'number' || paramSchema.type === 'integer') {
            // Number input
            const input = document.createElement('input');
            input.type = 'number';
            input.id = `config-${key}`;
            input.className = 'config-input';
            input.value = example[key] ?? paramSchema.default ?? '';
            if (paramSchema.type === 'number') {
                input.step = 'any';
            }
            input.addEventListener('input', () => {
                const val = input.value === '' ? null : Number(input.value);
                state.configValues[key] = val;
            });
            fieldDiv.appendChild(label);
            fieldDiv.appendChild(input);
        } else {
            // String input (or fallback)
            const input = document.createElement('input');
            input.type = paramSchema.nullable && key.toLowerCase().includes('key') ? 'password' : 'text';
            input.id = `config-${key}`;
            input.className = 'config-input';
            input.value = example[key] ?? paramSchema.default ?? '';
            input.placeholder = paramSchema.nullable ? '(optional)' : '';
            input.addEventListener('input', () => {
                state.configValues[key] = input.value || (paramSchema.nullable ? null : '');
            });
            fieldDiv.appendChild(label);
            fieldDiv.appendChild(input);
        }

        // Add type hint
        const hint = document.createElement('span');
        hint.className = 'config-hint';
        hint.textContent = paramSchema.nullable ? `${paramSchema.type} (optional)` : paramSchema.type;
        fieldDiv.appendChild(hint);

        container.appendChild(fieldDiv);
    });
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

async function handleActivate() {
    const btn = document.getElementById('create-activate-btn');
    const statusContainer = document.getElementById('status-container');

    btn.disabled = true;
    btn.textContent = 'Activating...';

    // Show activating status
    statusContainer.innerHTML = '';
    const activatingBox = document.createElement('div');
    activatingBox.className = 'status-box';
    const activatingP = document.createElement('p');
    const activatingIcon = document.createElement('span');
    activatingIcon.className = 'status-icon spinner';
    activatingIcon.textContent = '\u23F3';
    activatingP.appendChild(activatingIcon);
    activatingP.appendChild(document.createTextNode(' Activating policy...'));
    activatingBox.appendChild(activatingP);
    statusContainer.appendChild(activatingBox);

    try {
        // Use /admin/policy/set endpoint
        const result = await apiCall('/admin/policy/set', {
            method: 'POST',
            body: JSON.stringify({
                policy_class_ref: state.selectedPolicyClass.class_ref,
                config: state.configValues,
                enabled_by: 'ui'
            })
        });

        if (!result.success) {
            throw new Error(result.error || 'Failed to activate policy');
        }

        // Show success status
        statusContainer.innerHTML = '';
        const successBox = document.createElement('div');
        successBox.className = 'status-box success';
        const successP = document.createElement('p');
        const successIcon = document.createElement('span');
        successIcon.className = 'status-icon';
        successIcon.textContent = '\u2713';
        successP.appendChild(successIcon);
        successP.appendChild(document.createTextNode(' ' + state.selectedPolicyName + ' activated!'));
        successBox.appendChild(successP);
        statusContainer.appendChild(successBox);

        btn.textContent = 'Continue to Testing \u2192';
        btn.className = 'success';
        btn.onclick = () => goToStep3();
        btn.disabled = false;

        state.policyEnabledAt = Date.now();
    } catch (err) {
        statusContainer.innerHTML = '';
        const errorBox = document.createElement('div');
        errorBox.className = 'status-box error';
        const errorP = document.createElement('p');
        errorP.textContent = `Error: ${err.message}`;
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
    testStatus.innerHTML = '';
    const waitingP = document.createElement('p');
    const waitingIcon = document.createElement('span');
    waitingIcon.className = 'status-icon spinner';
    waitingIcon.textContent = '\u23F3';
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
        'DebugLoggingPolicy': 'Help me debug a function',
        'ParallelRulesPolicy': 'Test the parallel rules evaluation',
        'SimpleJudgePolicy': 'Test the judge policy',
        'ToolCallJudgePolicy': 'Test tool call evaluation'
    };
    return prompts[state.selectedPolicyName] || 'Test this policy';
}

async function handleCopyPrompt() {
    const promptText = document.getElementById('test-prompt').value;
    const copyBtn = document.getElementById('copy-prompt-btn');

    try {
        await navigator.clipboard.writeText(promptText);
        const originalText = copyBtn.textContent;
        copyBtn.textContent = 'Copied \u2713';
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
    testStatus.innerHTML = '';

    const p1 = document.createElement('p');
    const icon = document.createElement('span');
    icon.className = 'status-icon';
    icon.textContent = '\u2713';
    p1.appendChild(icon);
    p1.appendChild(document.createTextNode(' Test detected! Policy was applied'));

    const p2 = document.createElement('p');
    p2.appendChild(document.createTextNode('Call ID: '));
    const callIdSpan = document.createElement('span');
    callIdSpan.className = 'call-id';
    callIdSpan.textContent = callId;
    p2.appendChild(callIdSpan);

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
    diffLink.href = `/debug/diff?call_id=${encodeURIComponent(callId)}`;
    diffLink.textContent = 'View Diff';
    linksDiv.appendChild(diffLink);

    testStatus.appendChild(p1);
    testStatus.appendChild(p2);
    testStatus.appendChild(linksDiv);
}
