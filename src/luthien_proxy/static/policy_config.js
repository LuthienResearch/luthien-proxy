// State
const state = {
    currentPolicy: null,
    selectedPolicy: null,
    availablePolicies: [],
    availableModels: [],
    configValues: {},
    invalidFields: new Set(),  // Track fields with invalid JSON
    isActivating: false,
    isSending: false,
    selectedModel: null
};

// Check if current editor config matches the active policy
function isConfigMatchingActive() {
    if (!state.currentPolicy || !state.selectedPolicy) return false;
    if (state.currentPolicy.class_ref !== state.selectedPolicy.class_ref) return false;

    const activeConfig = state.currentPolicy.config || {};
    const editorConfig = state.configValues || {};

    return JSON.stringify(activeConfig) === JSON.stringify(editorConfig);
}

// Check if we're editing the same policy type as active
function isEditingActiveType() {
    return state.currentPolicy && state.selectedPolicy &&
        state.currentPolicy.class_ref === state.selectedPolicy.class_ref;
}

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    await Promise.all([loadCurrentPolicy(), loadPolicies(), loadModels()]);
    initTestPanel();
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

async function loadCurrentPolicy() {
    try {
        const data = await apiCall('/admin/policy/current');
        state.currentPolicy = data;
        renderCurrentPolicyBanner();
    } catch (err) {
        console.error('Failed to load current policy:', err);
        document.getElementById('current-policy-name').textContent = 'Error loading';
    }
}

async function loadPolicies() {
    try {
        const data = await apiCall('/admin/policy/list');
        state.availablePolicies = data.policies;
        renderPolicyList();
    } catch (err) {
        console.error('Failed to load policies:', err);
        document.getElementById('policy-list').innerHTML =
            `<div class="status-message error">Failed to load policies: ${err.message}</div>`;
    }
}

async function loadModels() {
    try {
        const data = await apiCall('/admin/models');
        state.availableModels = data.models || [];
        if (state.availableModels.length > 0) {
            state.selectedModel = state.availableModels[0];
        }
    } catch (err) {
        console.error('Failed to load models:', err);
        // Don't fall back to hardcoded models - let the user enter their own
        state.availableModels = [];
        state.selectedModel = null;
    }
}

function initTestPanel() {
    // Populate model selector datalist
    const modelSelect = document.getElementById('model-select');
    const modelDatalist = document.getElementById('model-datalist');
    if (modelSelect && modelDatalist) {
        // Populate datalist with available models
        modelDatalist.innerHTML = state.availableModels.map(m =>
            `<option value="${escapeHtml(m)}"></option>`
        ).join('');
        // Set initial value to first model if available
        if (state.selectedModel) {
            modelSelect.value = state.selectedModel;
        }
        // Update state when user changes the model
        modelSelect.addEventListener('input', (e) => {
            state.selectedModel = e.target.value.trim();
        });
    }

    // Toggle panel visibility
    const toggleBtn = document.getElementById('test-panel-toggle');
    const panelContent = document.getElementById('test-panel-content');
    if (toggleBtn && panelContent) {
        toggleBtn.addEventListener('click', () => {
            const isCollapsed = panelContent.classList.toggle('collapsed');
            toggleBtn.textContent = isCollapsed ? 'Show' : 'Hide';
        });
    }

    // Send button
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) {
        sendBtn.addEventListener('click', handleSendChat);
    }
}

function renderCurrentPolicyBanner() {
    const nameEl = document.getElementById('current-policy-name');
    const metaEl = document.getElementById('current-policy-meta');
    const configEl = document.getElementById('current-policy-config');

    if (state.currentPolicy) {
        nameEl.textContent = state.currentPolicy.policy;
        const parts = [];
        if (state.currentPolicy.enabled_by) {
            parts.push(`by ${state.currentPolicy.enabled_by}`);
        }
        if (state.currentPolicy.enabled_at) {
            const date = new Date(state.currentPolicy.enabled_at);
            parts.push(date.toLocaleString());
        }
        metaEl.textContent = parts.join(' • ');

        // Show config if present
        if (configEl) {
            const config = state.currentPolicy.config || {};
            const configKeys = Object.keys(config);
            if (configKeys.length > 0) {
                const configSummary = configKeys.map(k => {
                    const val = config[k];
                    const displayVal = typeof val === 'object' ? JSON.stringify(val) : String(val);
                    const truncated = displayVal.length > 30 ? displayVal.slice(0, 30) + '...' : displayVal;
                    return `${k}: ${truncated}`;
                }).join(', ');
                configEl.textContent = configSummary;
                configEl.style.display = 'block';
            } else {
                configEl.textContent = '(no config)';
                configEl.style.display = 'block';
            }
        }
    } else {
        nameEl.textContent = 'None';
        metaEl.textContent = '';
        if (configEl) {
            configEl.style.display = 'none';
        }
    }
}

function renderPolicyList() {
    const container = document.getElementById('policy-list');
    container.innerHTML = '';

    state.availablePolicies.forEach(policy => {
        const isActive = state.currentPolicy && state.currentPolicy.class_ref === policy.class_ref;
        const isSelected = state.selectedPolicy && state.selectedPolicy.class_ref === policy.class_ref;

        const item = document.createElement('div');
        item.className = 'policy-item';
        if (isActive) item.classList.add('active');
        if (isSelected) item.classList.add('selected');
        item.dataset.classRef = policy.class_ref;

        const header = document.createElement('div');
        header.className = 'policy-item-header';

        const name = document.createElement('div');
        name.className = 'policy-item-name';
        name.textContent = policy.name;

        header.appendChild(name);

        if (isActive) {
            const badge = document.createElement('span');
            badge.className = 'policy-item-badge active';
            badge.textContent = 'Active';
            header.appendChild(badge);
        }

        const desc = document.createElement('div');
        desc.className = 'policy-item-description';
        desc.textContent = policy.description;

        item.appendChild(header);
        item.appendChild(desc);

        const configKeys = Object.keys(policy.config_schema || {});
        if (configKeys.length > 0) {
            const configInfo = document.createElement('div');
            configInfo.className = 'policy-item-config';
            configInfo.textContent = `Config: ${configKeys.join(', ')}`;
            item.appendChild(configInfo);
        }

        item.addEventListener('click', () => selectPolicy(policy));
        container.appendChild(item);
    });
}

function selectPolicy(policy) {
    state.selectedPolicy = policy;
    state.invalidFields.clear();  // Reset invalid fields when selecting a new policy

    // If this is the currently active policy, use its actual config
    // Otherwise, use the example config as a starting point
    const isActivePolicy = state.currentPolicy && state.currentPolicy.class_ref === policy.class_ref;
    if (isActivePolicy && state.currentPolicy.config) {
        state.configValues = { ...state.currentPolicy.config };
    } else {
        state.configValues = { ...(policy.example_config || {}) };
    }

    // Update selection in list
    document.querySelectorAll('.policy-item').forEach(item => {
        item.classList.remove('selected');
        if (item.dataset.classRef === policy.class_ref) {
            item.classList.add('selected');
        }
    });

    renderConfigPanel();
}

function renderConfigPanel() {
    const panel = document.getElementById('config-panel');
    const policy = state.selectedPolicy;

    if (!policy) {
        panel.innerHTML = '<div class="config-panel-empty">Select a policy to configure</div>';
        return;
    }

    const isActiveType = isEditingActiveType();
    const isMatchingActive = isConfigMatchingActive();

    // Build status indicator
    let statusHtml = '';
    if (isActiveType) {
        if (isMatchingActive) {
            statusHtml = `<div class="config-status config-status-active">
                <span class="config-status-icon">✓</span>
                Viewing active policy configuration
            </div>`;
        } else {
            statusHtml = `<div class="config-status config-status-modified">
                <span class="config-status-icon">●</span>
                Configuration differs from active policy
            </div>`;
        }
    } else {
        statusHtml = `<div class="config-status config-status-different">
            <span class="config-status-icon">○</span>
            Different policy type from active
        </div>`;
    }

    let html = `
        <div class="selected-policy-name">${escapeHtml(policy.name)}</div>
        ${statusHtml}
        <div class="selected-policy-description">${escapeHtml(policy.description)}</div>
        <div class="config-form" id="config-form"></div>
        <div class="button-group">
            <button class="primary" id="activate-btn" ${state.isActivating ? 'disabled' : ''}>
                ${state.isActivating ? 'Activating...' : (isMatchingActive ? 'Reactivate Policy' : 'Activate Policy')}
            </button>
        </div>
        <div id="status-container"></div>
        <div class="quick-links">
            <h3>Quick Links</h3>
            <a href="/activity/monitor">View Activity Monitor</a>
            <a href="/debug/diff">View Diff Viewer</a>
        </div>
    `;

    panel.innerHTML = html;

    renderConfigForm(policy);
    document.getElementById('activate-btn').addEventListener('click', handleActivate);
}

function renderConfigForm(policy) {
    const container = document.getElementById('config-form');
    if (!container) return;

    const schema = policy.config_schema || {};
    const example = policy.example_config || {};
    const keys = Object.keys(schema);

    if (keys.length === 0) {
        container.innerHTML = '<p class="no-config-message">This policy has no configuration options.</p>';
        return;
    }

    container.innerHTML = '';

    keys.forEach(key => {
        const paramSchema = schema[key];
        const fieldDiv = document.createElement('div');
        fieldDiv.className = 'config-field';

        const label = document.createElement('label');
        label.textContent = key;
        label.setAttribute('for', `config-${key}`);

        const isComplex = paramSchema.type === 'object' || paramSchema.type === 'array';

        if (isComplex) {
            const textarea = document.createElement('textarea');
            textarea.id = `config-${key}`;
            textarea.className = 'config-input config-json';
            textarea.value = JSON.stringify(state.configValues[key] ?? example[key] ?? paramSchema.default ?? null, null, 2);

            // Error message element (hidden by default)
            const errorMsg = document.createElement('div');
            errorMsg.className = 'config-error-message';
            errorMsg.textContent = 'Invalid JSON - use double quotes for strings, e.g. [["hello", "world"]]';

            textarea.addEventListener('input', () => {
                try {
                    state.configValues[key] = JSON.parse(textarea.value);
                    textarea.classList.remove('invalid');
                    errorMsg.classList.remove('visible');
                    state.invalidFields.delete(key);
                } catch {
                    textarea.classList.add('invalid');
                    errorMsg.classList.add('visible');
                    state.invalidFields.add(key);
                }
                updateActivateButton();
                updateConfigStatus();
            });
            fieldDiv.appendChild(label);
            fieldDiv.appendChild(textarea);
            fieldDiv.appendChild(errorMsg);
        } else if (paramSchema.type === 'boolean') {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.id = `config-${key}`;
            checkbox.className = 'config-checkbox';
            checkbox.checked = state.configValues[key] ?? example[key] ?? paramSchema.default ?? false;
            checkbox.addEventListener('change', () => {
                state.configValues[key] = checkbox.checked;
                updateConfigStatus();
            });
            const checkboxWrapper = document.createElement('div');
            checkboxWrapper.className = 'checkbox-wrapper';
            checkboxWrapper.appendChild(checkbox);
            checkboxWrapper.appendChild(label);
            fieldDiv.appendChild(checkboxWrapper);
        } else if (paramSchema.type === 'number' || paramSchema.type === 'integer') {
            const input = document.createElement('input');
            input.type = 'number';
            input.id = `config-${key}`;
            input.className = 'config-input';
            input.value = state.configValues[key] ?? example[key] ?? paramSchema.default ?? '';
            if (paramSchema.type === 'number') {
                input.step = 'any';
            }
            input.addEventListener('input', () => {
                const val = input.value === '' ? null : Number(input.value);
                state.configValues[key] = val;
                updateConfigStatus();
            });
            fieldDiv.appendChild(label);
            fieldDiv.appendChild(input);
        } else {
            const input = document.createElement('input');
            input.type = paramSchema.nullable && key.toLowerCase().includes('key') ? 'password' : 'text';
            input.id = `config-${key}`;
            input.className = 'config-input';
            input.value = state.configValues[key] ?? example[key] ?? paramSchema.default ?? '';
            input.placeholder = paramSchema.nullable ? '(optional)' : '';
            input.addEventListener('input', () => {
                state.configValues[key] = input.value || (paramSchema.nullable ? null : '');
                updateConfigStatus();
            });
            fieldDiv.appendChild(label);
            fieldDiv.appendChild(input);
        }

        const hint = document.createElement('span');
        hint.className = 'config-hint';
        hint.textContent = paramSchema.nullable ? `${paramSchema.type} (optional)` : paramSchema.type;
        fieldDiv.appendChild(hint);

        container.appendChild(fieldDiv);
    });
}

function updateActivateButton() {
    const btn = document.getElementById('activate-btn');
    if (!btn) return;

    const hasInvalidFields = state.invalidFields.size > 0;
    btn.disabled = hasInvalidFields || state.isActivating;

    if (hasInvalidFields) {
        btn.textContent = 'Fix errors to activate';
        btn.classList.add('disabled-error');
    } else {
        btn.classList.remove('disabled-error');
        const isMatchingActive = isConfigMatchingActive();
        btn.textContent = state.isActivating ? 'Activating...' : (isMatchingActive ? 'Reactivate Policy' : 'Activate Policy');
    }
}

function updateConfigStatus() {
    const statusEl = document.querySelector('.config-status');
    if (!statusEl) return;

    const isActiveType = isEditingActiveType();
    const isMatchingActive = isConfigMatchingActive();

    // Update status classes and content
    statusEl.classList.remove('config-status-active', 'config-status-modified', 'config-status-different');

    if (isActiveType) {
        if (isMatchingActive) {
            statusEl.classList.add('config-status-active');
            statusEl.innerHTML = '<span class="config-status-icon">✓</span> Viewing active policy configuration';
        } else {
            statusEl.classList.add('config-status-modified');
            statusEl.innerHTML = '<span class="config-status-icon">●</span> Configuration differs from active policy';
        }
    } else {
        statusEl.classList.add('config-status-different');
        statusEl.innerHTML = '<span class="config-status-icon">○</span> Different policy type from active';
    }

    // Also update activate button text
    updateActivateButton();
}

async function handleActivate() {
    if (!state.selectedPolicy || state.isActivating) return;

    // Block activation if there are invalid fields
    if (state.invalidFields.size > 0) {
        const statusContainer = document.getElementById('status-container');
        statusContainer.innerHTML = '<div class="status-message error">Please fix JSON errors before activating</div>';
        return;
    }

    const btn = document.getElementById('activate-btn');
    const statusContainer = document.getElementById('status-container');

    state.isActivating = true;
    btn.disabled = true;
    btn.textContent = 'Activating...';
    statusContainer.innerHTML = '<div class="status-message info">Activating policy...</div>';

    try {
        const result = await apiCall('/admin/policy/set', {
            method: 'POST',
            body: JSON.stringify({
                policy_class_ref: state.selectedPolicy.class_ref,
                config: state.configValues,
                enabled_by: 'ui'
            })
        });

        if (!result.success) {
            throw new Error(result.error || 'Failed to activate policy');
        }

        // Refresh current policy
        await loadCurrentPolicy();
        renderPolicyList();

        statusContainer.innerHTML = `<div class="status-message success">✓ ${state.selectedPolicy.name} activated successfully</div>`;
        btn.textContent = 'Reactivate Policy';
    } catch (err) {
        statusContainer.innerHTML = `<div class="status-message error">Error: ${escapeHtml(err.message)}</div>`;
        btn.textContent = 'Retry Activation';
    } finally {
        state.isActivating = false;
        btn.disabled = false;
    }
}

async function handleSendChat() {
    if (state.isSending) return;

    const messageEl = document.getElementById('test-message');
    const responseEl = document.getElementById('test-response');
    const metaEl = document.getElementById('test-meta');
    const sendBtn = document.getElementById('send-btn');

    const message = messageEl.value.trim();
    if (!message) {
        responseEl.className = 'test-response error';
        responseEl.textContent = 'Please enter a message';
        return;
    }

    state.isSending = true;
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending...';
    responseEl.className = 'test-response empty';
    responseEl.textContent = 'Sending request...';
    metaEl.textContent = '';

    try {
        const result = await apiCall('/admin/test/chat', {
            method: 'POST',
            body: JSON.stringify({
                model: state.selectedModel,
                message: message,
                stream: false
            })
        });

        if (!result.success) {
            throw new Error(result.error || 'Request failed');
        }

        responseEl.className = 'test-response success';
        responseEl.textContent = result.content || '(empty response)';

        if (result.usage) {
            metaEl.textContent = `Model: ${result.model} • Tokens: ${result.usage.prompt_tokens} in / ${result.usage.completion_tokens} out`;
        } else {
            metaEl.textContent = `Model: ${result.model}`;
        }
    } catch (err) {
        responseEl.className = 'test-response error';
        responseEl.textContent = `Error: ${err.message}`;
    } finally {
        state.isSending = false;
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send Message';
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
