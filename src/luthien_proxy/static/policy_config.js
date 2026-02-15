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
    selectedModel: null,
    currentFormData: null  // For Alpine-based form data
};

// Initialize Alpine store for drawer state
document.addEventListener('alpine:init', () => {
    Alpine.store('drawer', {
        open: false,
        path: '',
        index: -1
    });
});

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
        window.__policyList = data.policies;
        renderPolicyList();
    } catch (err) {
        console.error('Failed to load policies:', err);
        document.getElementById('policy-list').innerHTML =
            `<div class="status-message error">Failed to load policies: ${err.message}</div>`;
    }
}

const DEFAULT_MODEL = 'claude-haiku-4-5-20241022';

async function loadModels() {
    try {
        const data = await apiCall('/admin/models');
        state.availableModels = data.models || [];
        // Default to claude-haiku if available, otherwise first model
        state.selectedModel = state.availableModels.find(m => m.includes('haiku'))
            || state.availableModels[0]
            || DEFAULT_MODEL;
    } catch (err) {
        console.error('Failed to load models:', err);
        state.availableModels = [];
        state.selectedModel = DEFAULT_MODEL;
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

function formatConfigValue(val, indent = 0) {
    const pad = '  '.repeat(indent);
    if (val === null || val === undefined) {
        return `<span class="config-val">null</span>`;
    }
    if (typeof val === 'boolean') {
        return `<span class="config-val-bool">${val}</span>`;
    }
    if (typeof val === 'number') {
        return `<span class="config-val-number">${val}</span>`;
    }
    if (typeof val === 'string') {
        return `<span class="config-val-string">"${escapeHtml(val)}"</span>`;
    }
    if (Array.isArray(val)) {
        if (val.length === 0) return `<span class="config-val">[]</span>`;
        const items = val.map(v => `${pad}  ${formatConfigValue(v, indent + 1)}`).join('\n');
        return `[\n${items}\n${pad}]`;
    }
    if (typeof val === 'object') {
        return formatConfigAsHtml(val, indent);
    }
    return `<span class="config-val">${escapeHtml(String(val))}</span>`;
}

function formatConfigAsHtml(config, indent = 0) {
    const pad = '  '.repeat(indent);
    const innerPad = '  '.repeat(indent + 1);
    const entries = Object.entries(config);
    if (entries.length === 0) return `<span class="config-val">{}</span>`;

    const lines = entries.map(([key, val]) => {
        return `${innerPad}<span class="config-key">${escapeHtml(key)}</span>: ${formatConfigValue(val, indent + 1)}`;
    });
    return `{\n${lines.join('\n')}\n${pad}}`;
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
                configEl.innerHTML = formatConfigAsHtml(config);
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

    // Check if any parameter schema has Pydantic structure (properties or $defs)
    // Schema is {paramName: paramSchema, ...} so we check each parameter
    const hasPydanticSchema = Object.values(schema).some(
        paramSchema => paramSchema && (paramSchema.properties || paramSchema.$defs || paramSchema['x-sub-policy-list'])
    );

    if (hasPydanticSchema && window.FormRenderer) {
        // Use new recursive renderer for Pydantic schemas
        renderWithAlpine(container, schema, example);
    } else {
        // Fallback to legacy rendering for non-Pydantic configs
        renderLegacyForm(container, schema, example);
    }
}

function renderWithAlpine(container, schema, initialData) {
    // Initialize form data with defaults from schema
    const formData = window.FormRenderer.getDefaultValue(schema, schema);
    // Merge initial data, but skip null values that would overwrite defaults
    for (const [key, value] of Object.entries(initialData || {})) {
        if (value !== null) {
            formData[key] = value;
        }
    }

    // Store in state for submission
    state.currentFormData = formData;
    // Also sync to configValues for consistency with existing code paths
    state.configValues = formData;

    // Generate form HTML
    const formHtml = window.FormRenderer.generateForm(schema);

    // Escape JSON for HTML attribute embedding (replace quotes with HTML entities)
    const escapedFormData = JSON.stringify(formData).replace(/"/g, '&quot;');

    // Create Alpine component
    container.innerHTML = `
        <div x-data="{ formData: ${escapedFormData} }"
             x-init="window.__alpineData = $data; $watch('formData', value => window.updateFormData(value)); window.initSubPolicyForms()">
            ${formHtml}
        </div>
    `;

    // Re-init Alpine on new content
    if (window.Alpine) {
        Alpine.initTree(container);
    }
}

// Global callback for Alpine data binding
window.updateFormData = function(data) {
    state.currentFormData = data;
    state.configValues = data;
    updateActivateButton();
    updateConfigStatus();
};

function renderLegacyForm(container, schema, example) {
    const keys = Object.keys(schema);

    if (keys.length === 0) {
        container.innerHTML = '<p class="no-config-message">This policy has no configuration options.</p>';
        return;
    }

    // Clear Alpine form data since we're using legacy rendering
    state.currentFormData = null;

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

// Global functions needed by FormRenderer
window.openDrawer = function(path, index) {
    if (window.Alpine) {
        Alpine.store('drawer').open = true;
        Alpine.store('drawer').path = path;
        Alpine.store('drawer').index = index;
    }
};

window.closeDrawer = function() {
    if (window.Alpine) {
        Alpine.store('drawer').open = false;
    }
};

window.validateJson = function(event, path) {
    // JSON validation for freeform objects
    try {
        JSON.parse(event.target.value);
        event.target.classList.remove('invalid');
    } catch (e) {
        event.target.classList.add('invalid');
    }
};

window.onUnionTypeChange = function(path, newType) {
    // TODO: Reset stale form fields when union type changes.
    // When the discriminator changes, fields from the previous variant remain
    // in formData. This should clear those and populate defaults for newType.
};

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

    // Clear previous validation errors
    clearValidationErrors();

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

        if (result.success) {
            // Refresh current policy
            await loadCurrentPolicy();
            renderPolicyList();

            statusContainer.innerHTML = `<div class="status-message success">✓ ${state.selectedPolicy.name} activated successfully</div>`;
            btn.textContent = 'Reactivate Policy';
        } else {
            // Handle validation errors
            if (result.validation_errors && result.validation_errors.length > 0) {
                highlightValidationErrors(result.validation_errors);
                statusContainer.innerHTML = '<div class="status-message error">Validation errors - check highlighted fields</div>';
            } else {
                statusContainer.innerHTML = `<div class="status-message error">Error: ${escapeHtml(result.error || 'Failed to activate policy')}</div>`;
            }
            btn.textContent = 'Retry Activation';
        }
    } catch (err) {
        statusContainer.innerHTML = `<div class="status-message error">Error: ${escapeHtml(err.message)}</div>`;
        btn.textContent = 'Retry Activation';
    } finally {
        state.isActivating = false;
        btn.disabled = false;
    }
}

function clearValidationErrors() {
    document.querySelectorAll('.field-error').forEach(el => el.remove());
    document.querySelectorAll('.form-field.has-error').forEach(el => {
        el.classList.remove('has-error');
    });
    document.querySelectorAll('.config-field.has-error').forEach(el => {
        el.classList.remove('has-error');
    });
}

function highlightValidationErrors(errors) {
    clearValidationErrors();

    for (const error of errors) {
        const path = error.loc.join('.');
        // Find field by path (data attribute or name or id)
        const field = document.querySelector(
            `[data-path="${path}"], [name="${path}"], #config-${path}`
        );
        if (field) {
            // Find the container - could be .form-field (Alpine) or .config-field (legacy)
            const container = field.closest('.form-field') || field.closest('.config-field');
            if (container) {
                container.classList.add('has-error');
                const errorEl = document.createElement('span');
                errorEl.className = 'field-error';
                errorEl.textContent = error.msg;
                container.appendChild(errorEl);
            }
        }
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

// =========================================================================
// Sub-policy list management
// =========================================================================

function getNestedValue(obj, path) {
    return path.split(/[.\[\]]/).filter(Boolean).reduce((o, k) => o?.[k], obj);
}

window.initSubPolicyForms = function() {
    document.querySelectorAll('.form-field-sub-policy-list').forEach(container => {
        const cardsContainer = container.querySelector('.sub-policy-cards');
        if (!cardsContainer) return;
        const path = cardsContainer.id.replace('sub-policy-cards-', '');
        window.renderAllSubPolicies(path);
    });
};

window.renderAllSubPolicies = function(path) {
    const data = window.__alpineData;
    if (!data) return;

    const policies = getNestedValue(data.formData, path) || [];
    const container = document.getElementById(`sub-policy-cards-${path}`);
    if (!container) return;

    let html = '';
    policies.forEach((subPolicy, index) => {
        html += renderSubPolicyCardHtml(path, index, subPolicy);
    });
    container.innerHTML = html;

    if (window.Alpine) {
        Alpine.initTree(container);
    }

    // Recursively initialize nested sub-policy lists (multi-policy inside multi-policy)
    container.querySelectorAll('[id^="sub-policy-cards-"]').forEach(nested => {
        const nestedPath = nested.id.replace('sub-policy-cards-', '');
        if (nestedPath !== path && nested.children.length === 0) {
            const nestedPolicies = getNestedValue(data.formData, nestedPath);
            if (nestedPolicies && nestedPolicies.length > 0) {
                window.renderAllSubPolicies(nestedPath);
            }
        }
    });
};

function renderSubPolicyCardHtml(path, index, subPolicy) {
    const policyList = window.__policyList || [];
    const selectedClass = subPolicy?.class || '';

    let options = '<option value="">Select a policy...</option>';
    policyList.forEach(p => {
        const selected = p.class_ref === selectedClass ? 'selected' : '';
        options += `<option value="${escapeHtml(p.class_ref)}" ${selected}>${escapeHtml(p.name)}</option>`;
    });

    let configHtml = '';
    if (selectedClass) {
        const policyInfo = policyList.find(p => p.class_ref === selectedClass);
        if (policyInfo && policyInfo.config_schema && Object.keys(policyInfo.config_schema).length > 0) {
            configHtml = window.FormRenderer.generateForm(
                policyInfo.config_schema, null, `${path}[${index}].config`
            );
        }
    }

    return `
        <div class="sub-policy-card" data-path="${path}" data-index="${index}">
            <div class="sub-policy-card-header">
                <select class="sub-policy-select"
                        x-model="formData.${path}[${index}].class"
                        @change="window.onSubPolicyClassChange('${path}', ${index}, $event.target.value)">
                    ${options}
                </select>
                <div class="sub-policy-card-actions">
                    <button type="button" class="btn-move" onclick="window.moveSubPolicy('${path}', ${index}, -1)" title="Move up">&uarr;</button>
                    <button type="button" class="btn-move" onclick="window.moveSubPolicy('${path}', ${index}, 1)" title="Move down">&darr;</button>
                    <button type="button" class="btn-remove-sub" onclick="window.removeSubPolicy('${path}', ${index})" title="Remove">&times;</button>
                </div>
            </div>
            <div class="sub-policy-config" id="sub-policy-config-${path}-${index}">
                ${configHtml}
            </div>
        </div>
    `;
}

window.onSubPolicyClassChange = function(path, index, classRef) {
    const data = window.__alpineData;
    if (!data) return;

    const policies = getNestedValue(data.formData, path);
    if (!policies || !policies[index]) return;

    policies[index].class = classRef;

    const policyList = window.__policyList || [];
    const policyInfo = policyList.find(p => p.class_ref === classRef);
    policies[index].config = policyInfo ? { ...(policyInfo.example_config || {}) } : {};

    const configContainer = document.getElementById(`sub-policy-config-${path}-${index}`);
    if (!configContainer) return;

    if (policyInfo && policyInfo.config_schema && Object.keys(policyInfo.config_schema).length > 0) {
        configContainer.innerHTML = window.FormRenderer.generateForm(
            policyInfo.config_schema, null, `${path}[${index}].config`
        );
        if (window.Alpine) {
            Alpine.initTree(configContainer);
        }
    } else {
        configContainer.innerHTML = '';
    }
};

window.addSubPolicy = function(path) {
    const data = window.__alpineData;
    if (!data) return;

    const policies = getNestedValue(data.formData, path);
    if (!policies) return;

    policies.push({ class: '', config: {} });
    window.renderAllSubPolicies(path);
};

window.removeSubPolicy = function(path, index) {
    const data = window.__alpineData;
    if (!data) return;

    const policies = getNestedValue(data.formData, path);
    if (!policies) return;

    policies.splice(index, 1);
    window.renderAllSubPolicies(path);
};

window.moveSubPolicy = function(path, index, direction) {
    const data = window.__alpineData;
    if (!data) return;

    const policies = getNestedValue(data.formData, path);
    if (!policies) return;

    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= policies.length) return;

    [policies[index], policies[newIndex]] = [policies[newIndex], policies[index]];
    window.renderAllSubPolicies(path);
};

// Shared HTML escaper — also used by FormRenderer
window.escapeHtml = function(text) {
    if (typeof text !== 'string') return text;
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
};
