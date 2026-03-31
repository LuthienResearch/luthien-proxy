// ============================================================
// Policy Configuration — Chain-first UI
// ============================================================

const NOOP_CLASS_REF = 'luthien_proxy.policies.noop_policy:NoOpPolicy';
const DEFAULT_MODEL = 'claude-haiku-4-5-20241022';
const MULTI_SERIAL_CLASS_REF = 'luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy';

// Policies shown in the "Simple" group (top of Available column)
const SIMPLE_POLICIES = [
    'AllCapsPolicy',
    'StringReplacementPolicy',
    'SimpleLLMPolicy',
    'ToolCallJudgePolicy',
];

// Hidden from default view — internal/meta policies users don't pick directly.
// Names must match the `name` field from the /api/admin/policy/list discovery API.
const HIDDEN_POLICIES = new Set([
    'DebugLoggingPolicy',
    'NoOpPolicy',
    'SimpleNoOpPolicy',
    'PlainDashesPolicy',
    'MultiSerialPolicy',
    'SimplePolicy',
    'SamplePydanticPolicy',
]);

// Inline examples shown on policy cards
const EXAMPLES = {
    'luthien_proxy.policies.noop_policy:NoOpPolicy': {
        desc: null,
        input: 'Hello, how are you?',
        output: 'Hello, how are you?',
    },
    'luthien_proxy.policies.all_caps_policy:AllCapsPolicy': {
        desc: null,
        input: 'Hello, how are you?',
        output: 'HELLO, HOW ARE YOU?',
    },
    'luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy': {
        desc: null,
        input: 'Hello, how are you?',
        output: 'Hello, how are you?  (request & response logged)',
    },
    'luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy': {
        desc: 'Config: replacements = [["quick", "fast"], ["fox", "cat"]]',
        input: 'The quick brown fox jumps',
        output: 'The fast brown cat jumps',
    },
    'luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy': {
        desc: 'Config: model = "gpt-4o-mini", threshold = 0.01',
        input: 'Tool call: run_shell("rm -rf /")',
        output: '\u26d4 Tool \'run_shell\' blocked: Destructive filesystem operation',
    },
    'luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy': {
        desc: 'Config: model, instructions for the policy LLM',
        input: 'User request to write code',
        output: '[Policy LLM evaluates request against instructions]',
    },
    'luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy': {
        desc: null,
        input: 'Delete all user data from the database',
        output: '\u26d4 Blocked: Safety constraint violation',
    },
    'luthien_proxy.policies.simple_policy:SimplePolicy': {
        desc: null,
        input: 'Hello!',
        output: 'Hello!  (non-streaming)',
    },
};

// ============================================================
// State
// ============================================================
const state = {
    policies: [],
    currentPolicy: null,
    invalidFields: new Set(),
    chain: [],
    availableModels: [],
    selectedModel: DEFAULT_MODEL,
    isActivating: false,
    cachedCredentials: [],
    credentialSource: 'server',
    showHidden: false,
    expandedChainIndex: -1,
    expandedAvailablePolicy: null,
};

// Order-insensitive deep equality for config objects
function configEqual(a, b) {
    if (a === b) return true;
    if (a == null || b == null) return a == b;
    if (typeof a !== typeof b) return false;
    if (typeof a !== 'object') return a === b;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    if (Array.isArray(a)) {
        if (a.length !== b.length) return false;
        return a.every((v, i) => configEqual(v, b[i]));
    }
    const keysA = Object.keys(a), keysB = Object.keys(b);
    if (keysA.length !== keysB.length) return false;
    return keysA.every(k => Object.hasOwn(b, k) && configEqual(a[k], b[k]));
}

// Preserve test results across re-renders
const testState = { proposed: {}, active: {} };

// ============================================================
// API helpers
// ============================================================
async function apiCall(endpoint, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    const response = await fetch(endpoint, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
    if (response.status === 403) {
        window.location.href = '/login?error=required&next=' + encodeURIComponent(window.location.pathname);
        throw new Error('Session expired');
    }
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(error.detail || 'Request failed');
    }
    return response.json();
}

// Shared HTML escaper — also used by FormRenderer
window.escapeHtml = function(text) {
    return esc(text);
};

function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ============================================================
// Bootstrap
// ============================================================

document.addEventListener('alpine:init', () => {
    Alpine.store('drawer', { open: false, path: '', index: -1 });
});

document.addEventListener('DOMContentLoaded', async () => {
    initFilterInput();
    await Promise.all([loadPolicies(), loadCurrentPolicy(), loadModels(), loadGatewaySettings()]);
    renderAll();
});

async function loadPolicies() {
    try {
        const data = await apiCall('/api/admin/policy/list');
        state.policies = data.policies || [];
        window.__policyList = state.policies;
    } catch (err) {
        console.error('Failed to load policies:', err);
        document.getElementById('available-list').innerHTML =
            `<div class="status-msg error">Failed to load policies: ${esc(err.message)}</div>`;
    }
}

async function loadCurrentPolicy() {
    try {
        state.currentPolicy = await apiCall('/api/admin/policy/current');
    } catch (err) {
        console.error('Failed to load current policy:', err);
    }
}

async function loadModels() {
    try {
        const data = await apiCall('/api/admin/models');
        state.availableModels = data.models || [];
        state.selectedModel = state.availableModels.find(m => m.includes('haiku'))
            || state.availableModels[0] || DEFAULT_MODEL;
    } catch {
        state.availableModels = [];
    }
}

async function loadGatewaySettings() {
    try {
        const data = await apiCall('/api/admin/gateway/settings');
        document.getElementById('set-inject').checked = data.inject_policy_context;
        document.getElementById('set-dogfood').checked = data.dogfood_mode;
    } catch (e) {
        console.warn('Failed to load gateway settings:', e);
    }
}

async function loadCredentials() {
    try {
        const data = await apiCall('/api/admin/auth/credentials');
        state.cachedCredentials = (data.credentials || []).filter(c => c.valid);
    } catch {
        state.cachedCredentials = [];
    }
}

// ============================================================
// Lookup helpers
// ============================================================
function getPolicy(classRef) {
    return state.policies.find(p => p.class_ref === classRef);
}

function configParamCount(policy) {
    if (!policy || !policy.config_schema) return 0;
    return Object.keys(policy.config_schema).length;
}

function isCurrentActive(classRef) {
    return state.currentPolicy && state.currentPolicy.class_ref === classRef;
}

function isInActiveChain(classRef) {
    if (!state.currentPolicy) return false;
    const cp = state.currentPolicy;
    if (cp.class_ref !== MULTI_SERIAL_CLASS_REF) return false;
    const policies = cp.config && cp.config.policies;
    if (!Array.isArray(policies)) return false;
    return policies.some(sub => (sub.class_ref || sub.class) === classRef);
}

function defaultConfigFor(policy) {
    return { ...(policy.example_config || {}) };
}


async function saveGatewaySetting(field, value) {
    try {
        await apiCall('/api/admin/gateway/settings', {
            method: 'PUT',
            body: JSON.stringify({ [field]: value })
        });
    } catch (e) {
        console.error('Failed to save gateway setting:', e);
        const elId = field === 'inject_policy_context' ? 'set-inject' : 'set-dogfood';
        document.getElementById(elId).checked = !value;
    }
}

// ============================================================
// Filter
// ============================================================
function initFilterInput() {
    document.getElementById('filter-input').addEventListener('input', renderAvailable);
}

function toggleShowHidden() {
    state.showHidden = !state.showHidden;
    renderAvailable();
}

// ============================================================
// Policy interaction — expand details or add to chain
// ============================================================
function togglePolicyExpand(classRef) {
    state.expandedAvailablePolicy = (state.expandedAvailablePolicy === classRef) ? null : classRef;
    renderAvailable();
}

function addToChain(classRef, event) {
    if (event) event.stopPropagation();
    const p = getPolicy(classRef);
    if (!p) return;
    state.chain.push({ classRef, config: defaultConfigFor(p) });
    state.expandedChainIndex = state.chain.length - 1;
    state.invalidFields.clear();
    renderAll();
}

// ============================================================
// Example block rendering
// ============================================================
function renderExampleBlock(classRef, large) {
    const ex = EXAMPLES[classRef];
    if (!ex) return '';
    const cls = large ? 'example-block-proposed' : 'example-block';
    let html = `<div class="${cls}">`;
    if (ex.desc) {
        html += `<div class="example-config-desc">${esc(ex.desc)}</div>`;
    }
    html += `<div class="example-io"><span class="ex-label">In:</span> "${esc(ex.input)}"</div>`;
    html += `<div class="example-io"><span class="ex-label">Out:</span> "${esc(ex.output)}"</div>`;
    html += '</div>';
    return html;
}

// ============================================================
// Render: Available column (with Simple/Advanced grouping)
// ============================================================
function renderAvailable() {
    const filter = document.getElementById('filter-input').value.toLowerCase();
    const list = document.getElementById('available-list');
    list.innerHTML = '';

    const simple = [];
    const advanced = [];
    let hiddenCount = 0;

    for (const p of state.policies) {
        if (filter && !p.name.toLowerCase().includes(filter) && !(p.description || '').toLowerCase().includes(filter)) continue;
        const isHidden = HIDDEN_POLICIES.has(p.name);
        if (isHidden) hiddenCount++;
        if (!state.showHidden && isHidden) continue;
        if (SIMPLE_POLICIES.includes(p.name)) {
            simple.push(p);
        } else {
            advanced.push(p);
        }
    }

    simple.sort((a, b) => SIMPLE_POLICIES.indexOf(a.name) - SIMPLE_POLICIES.indexOf(b.name));

    if (state.chain.length === 0) {
        const hint = document.createElement('div');
        hint.className = 'available-hint';
        hint.textContent = 'Press + on a policy to add it to your chain';
        list.appendChild(hint);
    }

    if (simple.length > 0) {
        const label = document.createElement('div');
        label.className = 'group-label';
        label.textContent = 'Simple';
        list.appendChild(label);
        for (const p of simple) renderPolicyCard(p, list);
    }

    if (advanced.length > 0) {
        const label = document.createElement('div');
        label.className = 'group-label';
        label.textContent = 'Advanced';
        list.appendChild(label);
        for (const p of advanced) renderPolicyCard(p, list);
    }

    if (hiddenCount > 0) {
        const toggle = document.createElement('button');
        toggle.className = 'show-hidden-btn';
        toggle.textContent = state.showHidden ? 'Hide internal policies' : `Show ${hiddenCount} internal policies`;
        toggle.onclick = toggleShowHidden;
        list.appendChild(toggle);
    }

    if (list.children.length === 0 && state.policies.length > 0) {
        list.innerHTML = '<div class="empty-state">No policies match filter</div>';
    }
}

function renderPolicyCard(p, container) {
    const div = document.createElement('div');
    div.className = 'policy-card';
    div.setAttribute('role', 'button');
    div.setAttribute('tabindex', '0');
    const inActiveChain = isInActiveChain(p.class_ref);
    if (isCurrentActive(p.class_ref) || inActiveChain) div.className += ' is-active';

    const inChain = state.chain.some(c => c.classRef === p.class_ref);
    if (inChain) div.className += ' in-chain';

    const isExpanded = state.expandedAvailablePolicy === p.class_ref;
    if (isExpanded) div.className += ' expanded';

    const cc = configParamCount(p);
    const configHint = cc > 0 ? ` <span class="p-config-hint">(${cc} setting${cc > 1 ? 's' : ''})</span>` : '';
    const dot = isCurrentActive(p.class_ref) ? '<span class="active-dot" title="Currently active"></span>' : '';

    const firstLine = (p.description || '').split('.')[0];
    const exampleHtml = renderExampleBlock(p.class_ref, false);

    let expandedHtml = '';
    if (isExpanded) {
        const fullDesc = p.description || '';
        expandedHtml = `<div class="p-full-desc">${esc(fullDesc)}</div>`;
        expandedHtml += exampleHtml;
        if (cc > 0) expandedHtml += `<div class="p-config-detail">${cc} configurable setting${cc > 1 ? 's' : ''}</div>`;
    }

    div.innerHTML = `
        <div class="p-name">${dot}${esc(p.name)}<button class="p-add-btn">+<span class="p-add-tooltip">Add to policy chain</span></button></div>
        <div class="p-desc">${esc(firstLine)}.${configHint}</div>${isExpanded ? expandedHtml : ''}`;
    div.querySelector('.p-add-btn').onclick = (e) => addToChain(p.class_ref, e);
    div.onclick = () => togglePolicyExpand(p.class_ref);
    div.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); togglePolicyExpand(p.class_ref); } };
    container.appendChild(div);
}

// ============================================================
// Render: Proposed column — always chain view
// ============================================================
function renderProposed() {
    const col = document.getElementById('col-proposed');
    const hasContent = state.chain.length > 0;
    col.className = 'column' + (hasContent ? ' col-proposed has-content' : '');

    const empty = document.getElementById('proposed-empty');
    const content = document.getElementById('proposed-content');

    if (state.chain.length === 0) {
        empty.innerHTML = '<div class="empty-chain-state">' +
            '<div class="empty-chain-icon">+</div>' +
            '<div class="empty-chain-title">Build a Policy Chain</div>' +
            '<div class="empty-chain-desc">Click policies on the left to add them here. ' +
            'Policies run in order, top to bottom.</div>' +
            '</div>';
        empty.style.display = ''; content.style.display = 'none';
        return;
    }

    empty.style.display = 'none'; content.style.display = '';

    let html = '<div class="chain-header-row">';
    html += '<span class="chain-title">Policy Chain</span>';
    html += `<span class="chain-count">${state.chain.length} ${state.chain.length === 1 ? 'policy' : 'policies'}</span>`;
    html += '</div>';

    html += '<ul class="chain-list">';
    for (let i = 0; i < state.chain.length; i++) {
        const item = state.chain[i];
        const p = getPolicy(item.classRef);
        if (!p) continue;
        const isExpanded = state.expandedChainIndex === i;
        const hasConfig = configParamCount(p) > 0;

        html += `<li class="chain-item${isExpanded ? ' expanded' : ''}">`;
        html += '<div class="chain-item-header">';
        html += `<span class="chain-num">${i + 1}</span>`;
        html += `<span class="chain-item-name" onclick="toggleChainExpand(${i})">${esc(p.name)}</span>`;
        html += '<span class="chain-actions">';
        html += `<button class="chain-btn" onclick="event.stopPropagation();moveChain(${i},-1)" title="Move up" ${i === 0 ? 'disabled' : ''}>&uarr;</button>`;
        html += `<button class="chain-btn" onclick="event.stopPropagation();moveChain(${i},1)" title="Move down" ${i === state.chain.length - 1 ? 'disabled' : ''}>&darr;</button>`;
        html += `<button class="chain-btn chain-btn-remove" onclick="event.stopPropagation();removeChain(${i})" title="Remove">&times;</button>`;
        html += '</span></div>';

        if (isExpanded) {
            html += `<div class="chain-item-desc">${esc(p.description || '')}</div>`;
            if (hasConfig) {
                html += `<div id="chain-config-${i}" class="chain-config-container"></div>`;
            } else {
                html += '<div class="no-config">No configuration needed</div>';
            }
        } else if (hasConfig) {
            html += `<div class="chain-item-config-hint" onclick="toggleChainExpand(${i})">Click to configure</div>`;
        }

        html += '</li>';
        if (i < state.chain.length - 1) html += '<div class="chain-divider">&darr;</div>';
    }
    html += '</ul>';

    html += '<div class="chain-add-hint">Press <strong>+</strong> on any policy in the Available panel to add it here</div>';

    html += '<div id="proposed-status"></div>';
    const activateLabel = state.chain.length > 1
        ? `Activate Chain (${state.chain.length} policies)`
        : 'Activate';
    html += `<button class="btn-activate" id="btn-activate" onclick="handleActivateChain()">${activateLabel}</button>`;
    html += renderTestSection('proposed');
    content.innerHTML = html;
    for (let i = 0; i < state.chain.length; i++) {
        if (state.expandedChainIndex === i) {
            renderChainItemConfig(i);
        }
    }
    bindTestSection('proposed');
}

function moveChain(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= state.chain.length) return;
    [state.chain[i], state.chain[j]] = [state.chain[j], state.chain[i]];
    // Track expanded item through the move
    if (state.expandedChainIndex === i) state.expandedChainIndex = j;
    else if (state.expandedChainIndex === j) state.expandedChainIndex = i;
    renderProposed();
}

function removeChain(i) {
    state.chain.splice(i, 1);
    if (state.expandedChainIndex === i) {
        state.expandedChainIndex = -1;
    } else if (state.expandedChainIndex > i) {
        state.expandedChainIndex--;
    }
    renderAll();
}

function toggleChainExpand(i) {
    state.expandedChainIndex = (state.expandedChainIndex === i) ? -1 : i;
    renderProposed();
}

function renderChainItemConfig(i) {
    const item = state.chain[i];
    if (!item) return;
    const p = getPolicy(item.classRef);
    if (!p || configParamCount(p) === 0) return;

    const container = document.getElementById(`chain-config-${i}`);
    if (!container) return;

    const schema = p.config_schema || {};
    const hasPydanticSchema = Object.values(schema).some(
        ps => ps && (ps.properties || ps.$defs || ps['x-sub-policy-list'] ||
            (ps.type === 'array' && ps.items?.type === 'array'))
    );

    if (hasPydanticSchema && window.FormRenderer) {
        const formData = window.FormRenderer.getDefaultValue(schema, schema);
        for (const [key, value] of Object.entries(p.example_config || {})) {
            if (value !== null) formData[key] = value;
        }
        for (const [key, value] of Object.entries(item.config || {})) {
            if (value !== null && value !== undefined) formData[key] = value;
        }
        item.config = formData;

        const formHtml = window.FormRenderer.generateForm(schema);
        const escapedFormData = JSON.stringify(formData)
            .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
            .replace(/</g, '&lt;').replace(/>/g, '&gt;');

        container.innerHTML = `
            <div class="config-form" x-data="{ formData: ${escapedFormData} }"
                 x-init="$watch('formData', value => updateChainConfig(${i}, value))">
                ${formHtml}
            </div>`;
        if (window.Alpine) Alpine.initTree(container);
    } else {
        container.innerHTML = `<div class="config-form">${renderLegacyConfigFormInner(p, item.config, 'chain-' + i)}</div>`;
        bindLegacyConfigInputs('chain-' + i);
    }
}

function updateChainConfig(index, data) {
    if (state.chain[index]) {
        state.chain[index].config = data;
        updateActivateButton();
    }
}

// ============================================================
// Config form rendering (legacy fallback for non-Pydantic schemas)
// ============================================================
function renderLegacyConfigFormInner(policy, config, prefix) {
    const schema = policy.config_schema || {};
    const keys = Object.keys(schema);
    if (keys.length === 0) return '';

    let html = '';
    for (const key of keys) {
        const ps = schema[key];
        const val = config[key] ?? ps.default ?? '';
        const isComplex = ps.type === 'object' || ps.type === 'array';
        const isPassword = ps.type === 'string' && key.toLowerCase().includes('key');

        if (ps.type === 'boolean') {
            const checked = (config[key] ?? ps.default ?? false) ? 'checked' : '';
            html += `<div class="checkbox-row">
                <input type="checkbox" data-prefix="${prefix}" data-key="${key}" ${checked}>
                <label>${esc(key)}</label>
            </div>`;
        } else if (ps.type === 'number' || ps.type === 'integer') {
            const step = ps.type === 'number' ? ' step="any"' : '';
            html += `<label>${esc(key)}</label>`;
            html += `<input type="number"${step} data-prefix="${prefix}" data-key="${key}" value="${esc(val)}">`;
            if (ps.nullable) html += `<span class="config-hint">${esc(ps.type)} (optional)</span>`;
        } else if (isComplex) {
            html += `<label>${esc(key)}</label>`;
            const jsonVal = JSON.stringify(config[key] ?? ps.default ?? null, null, 2);
            html += `<textarea data-prefix="${prefix}" data-key="${key}" data-json="true">${esc(jsonVal)}</textarea>`;
            html += `<div class="config-error-msg" data-error-for="${prefix}-${key}">Invalid JSON</div>`;
            html += `<span class="config-hint">${esc(ps.type)}</span>`;
        } else {
            html += `<label>${esc(key)}</label>`;
            const inputType = isPassword ? 'password' : 'text';
            const placeholder = ps.nullable ? '(optional)' : '';
            html += `<input type="${inputType}" data-prefix="${prefix}" data-key="${key}" value="${esc(val)}" placeholder="${placeholder}">`;
            if (ps.nullable) html += `<span class="config-hint">${esc(ps.type)} (optional)</span>`;
        }
    }
    return html;
}

function bindLegacyConfigInputs(prefix) {
    document.querySelectorAll(`[data-prefix="${prefix}"][data-key]`).forEach(el => {
        el.addEventListener('input', () => {
            const key = el.dataset.key;
            const isJson = el.dataset.json === 'true';
            const isCheckbox = el.type === 'checkbox';
            const isNumber = el.type === 'number';
            const fieldId = `${prefix}-${key}`;

            let val;
            if (isCheckbox) {
                val = el.checked;
            } else if (isJson) {
                try {
                    val = JSON.parse(el.value);
                    el.classList.remove('invalid');
                    const errEl = document.querySelector(`[data-error-for="${fieldId}"]`);
                    if (errEl) errEl.classList.remove('visible');
                    state.invalidFields.delete(fieldId);
                } catch {
                    el.classList.add('invalid');
                    const errEl = document.querySelector(`[data-error-for="${fieldId}"]`);
                    if (errEl) errEl.classList.add('visible');
                    state.invalidFields.add(fieldId);
                    updateActivateButton();
                    return;
                }
            } else if (isNumber) {
                val = el.value === '' ? null : Number(el.value);
            } else {
                val = el.value;
            }

            if (prefix.startsWith('chain-')) {
                const idx = parseInt(prefix.split('-')[1]);
                state.chain[idx].config[key] = val;
            }
            updateActivateButton();
        });

        if (el.type === 'checkbox') {
            el.addEventListener('change', () => el.dispatchEvent(new Event('input')));
        }
    });
}

function updateActivateButton() {
    const btn = document.getElementById('btn-activate');
    if (!btn) return;

    if (state.invalidFields.size > 0) {
        btn.disabled = true;
        btn.textContent = 'Fix errors to activate';
        btn.classList.add('error-state');
        return;
    }
    btn.classList.remove('error-state');

    btn.disabled = state.isActivating;
    const label = state.chain.length > 1
        ? `Activate Chain (${state.chain.length} policies)`
        : 'Activate';
    btn.textContent = state.isActivating ? 'Activating...' : label;
}

// ============================================================
// Render: Active column
// ============================================================
function renderActive() {
    const body = document.getElementById('active-body');
    if (!state.currentPolicy) {
        body.innerHTML = '<div class="empty-state">No active policy</div>';
        return;
    }

    const cp = state.currentPolicy;
    const isChain = cp.class_ref === MULTI_SERIAL_CLASS_REF;
    const p = getPolicy(cp.class_ref);

    const name = isChain ? 'Chain' : (p ? p.name : cp.policy);
    const desc = isChain ? '' : (p ? p.description : '');

    let html = '<div class="active-label">Currently Running</div>';
    html += `<div class="active-name">${esc(name)}</div>`;
    if (desc) html += `<div class="active-desc">${esc(desc)}</div>`;

    const config = cp.config || {};

    if (isChain && config.policies && Array.isArray(config.policies)) {
        html += '<div class="active-chain-list">';
        config.policies.forEach((sub, i) => {
            const classRef = sub.class_ref || sub.class;
            const subPolicy = getPolicy(classRef);
            const subName = subPolicy ? subPolicy.name : (classRef || 'Unknown').split(':').pop();
            html += '<div class="active-chain-item">';
            html += `<span class="chain-num">${i + 1}</span>`;
            html += `<span>${esc(subName)}</span>`;
            html += '</div>';
            if (sub.config && Object.keys(sub.config).length > 0) {
                html += `<div class="active-chain-item-config">${formatConfigHtml(sub.config)}</div>`;
            }
        });
        html += '</div>';
    } else {
        const configKeys = Object.keys(config);
        if (configKeys.length > 0) {
            html += '<div class="active-config">';
            html += formatConfigHtml(config);
            html += '</div>';
        }
    }

    const parts = [];
    if (cp.enabled_by) parts.push(`by ${esc(cp.enabled_by)}`);
    if (cp.enabled_at) {
        const d = new Date(cp.enabled_at);
        parts.push(d.toLocaleString());
    }
    if (parts.length > 0) {
        html += `<div class="active-meta">${parts.join(' &middot; ')}</div>`;
    }

    if (cp.class_ref !== NOOP_CLASS_REF) {
        html += '<button class="deactivate-link" onclick="handleDeactivate()">Deactivate</button>';
    }
    html += '<div id="active-status"></div>';

    html += renderTestSection('active');
    body.innerHTML = html;
    bindTestSection('active');
}

function formatConfigHtml(config) {
    const entries = Object.entries(config);
    if (entries.length === 0) return '';
    let html = '';
    for (const [key, val] of entries) {
        html += `<div class="cfg-row"><span class="cfg-key">${esc(key)}:</span>`;
        html += `<span class="cfg-val">${formatValue(val)}</span></div>`;
    }
    return html;
}

function formatValue(val) {
    if (val === null || val === undefined) return 'null';
    if (typeof val === 'boolean') return `<span class="cfg-val-bool">${val}</span>`;
    if (typeof val === 'number') return `<span class="cfg-val-number">${val}</span>`;
    if (typeof val === 'string') {
        const display = val.length > 60 ? val.slice(0, 60) + '...' : val;
        return `<span class="cfg-val-string">"${esc(display)}"</span>`;
    }
    if (Array.isArray(val)) return esc(JSON.stringify(val));
    if (typeof val === 'object') return esc(JSON.stringify(val));
    return esc(String(val));
}

// ============================================================
// Credential management
// ============================================================
function timeSince(timestamp) {
    const seconds = Math.floor(Date.now() / 1000 - timestamp);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
    return Math.floor(seconds / 86400) + 'd ago';
}

function onCredSourceChange(side, value) {
    state.credentialSource = value;
    ['proposed', 'active'].forEach(s => {
        const sel = document.getElementById(`cred-select-${s}`);
        if (sel) sel.value = value;
    });
    renderAll();
}

// ============================================================
// Test section
// ============================================================
function renderTestSection(side) {
    const hasAnyCreds = state.cachedCredentials.length > 0 || state.credentialSource === 'custom';
    let html = '<div class="test-section">';
    html += '<div class="test-section-title">Test This Policy</div>';

    html += '<div class="test-cred-row">';
    html += '<label class="test-cred-label">Credentials:</label>';
    html += `<select class="test-cred-select" id="cred-select-${side}" onchange="onCredSourceChange('${side}', this.value)">`;
    html += '<option value="server"' + (state.credentialSource === 'server' ? ' selected' : '') + '>Server API Key</option>';
    html += '<option value="custom"' + (state.credentialSource === 'custom' ? ' selected' : '') + '>Enter API key...</option>';
    html += '</select>';
    html += '</div>';

    if (state.credentialSource === 'custom') {
        html += `<div class="test-cred-custom">
            <input type="password" class="credential-input" id="cred-input-${side}"
                placeholder="sk-ant-... or OAuth token">
        </div>`;
    }

    html += `<div id="test-inner-${side}">`;
    html += `<div class="test-model-row">
        <input type="text" class="test-model-input" id="test-model-${side}"
            placeholder="Model..." value="${esc(state.selectedModel)}"
            list="model-list-${side}">
        <datalist id="model-list-${side}">
            ${state.availableModels.map(m => `<option value="${esc(m)}">`).join('')}
        </datalist>
    </div>
    <div class="test-mock-row">
        <label class="test-mock-label">
            <input type="checkbox" id="test-mock-${side}" class="test-mock-checkbox">
            <span>Mock</span>
            <span class="test-mock-info" title="Skip the real LLM call. The server echoes your message back as the response (no API credits used). Note: the policy pipeline does not currently run on mock responses — this shows the raw echo only.">&#9432;</span>
        </label>
    </div>
    <div class="test-input-row">
        <textarea class="test-textarea" id="test-input-${side}" placeholder="Enter test message..." rows="2"
            onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();runTest('${side}');}"></textarea>
        <button class="test-run-btn" id="test-btn-${side}" onclick="runTest('${side}')">Send</button>
    </div>
    <div class="test-results" id="test-results-${side}">
        <div>
            <div class="test-box-label">Input</div>
            <div class="test-box test-box-input" id="test-box-in-${side}"></div>
        </div>
        <div>
            <div class="test-box-label">Output</div>
            <div class="test-box test-box-output" id="test-box-out-${side}"></div>
        </div>
    </div>
    <div class="test-meta" id="test-meta-${side}"></div>`;
    html += '</div>';
    html += '</div>';
    return html;
}

function bindTestSection(side) {
    const input = document.getElementById(`test-input-${side}`);
    const results = document.getElementById(`test-results-${side}`);
    const boxIn = document.getElementById(`test-box-in-${side}`);
    const boxOut = document.getElementById(`test-box-out-${side}`);
    if (!input || !results) return;
    if (testState[side] && testState[side].inputText) {
        input.value = testState[side].inputText;
    }
    if (testState[side] && testState[side].ran) {
        boxIn.textContent = testState[side].originalInput;
        boxOut.innerHTML = testState[side].outputHtml;
        results.classList.add('visible');
        const meta = document.getElementById(`test-meta-${side}`);
        if (meta && testState[side].metaText) meta.textContent = testState[side].metaText;
    }
}

async function runTest(side) {
    const input = document.getElementById(`test-input-${side}`);
    const results = document.getElementById(`test-results-${side}`);
    const boxIn = document.getElementById(`test-box-in-${side}`);
    const boxOut = document.getElementById(`test-box-out-${side}`);
    const meta = document.getElementById(`test-meta-${side}`);
    const btn = document.getElementById(`test-btn-${side}`);
    const modelInput = document.getElementById(`test-model-${side}`);
    const msg = input.value.trim();
    if (!msg) return;

    const model = modelInput ? modelInput.value.trim() : state.selectedModel;

    btn.disabled = true;
    btn.textContent = '...';
    boxIn.textContent = msg;
    boxOut.textContent = 'Sending...';
    results.classList.add('visible');
    meta.textContent = '';

    try {
        const mockCheckbox = document.getElementById(`test-mock-${side}`);
        const useMock = mockCheckbox ? mockCheckbox.checked : false;
        const testPayload = {
            model: model || state.selectedModel,
            message: msg,
            stream: false,
            use_mock: useMock,
        };
        if (state.credentialSource === 'custom') {
            const credInput = document.getElementById(`cred-input-${side}`);
            const key = credInput ? credInput.value.trim() : '';
            if (key) testPayload.api_key = key;
        }
        const result = await apiCall('/api/admin/test/chat', {
            method: 'POST',
            body: JSON.stringify(testPayload)
        });

        if (!result.success) throw new Error(result.error || 'Request failed');

        const content = result.content || '(empty response)';
        boxOut.textContent = content;
        boxOut.style.color = '';

        let metaText = `Model: ${result.model || model}`;
        if (result.usage) {
            metaText += ` | ${result.usage.prompt_tokens} in / ${result.usage.completion_tokens} out`;
        }
        meta.textContent = metaText;

        testState[side] = {
            inputText: input.value,
            originalInput: msg,
            outputHtml: esc(content),
            metaText: metaText,
            ran: true,
        };
    } catch (err) {
        boxOut.textContent = `Error: ${err.message}`;
        boxOut.style.color = '#fca5a5';
        testState[side] = {
            inputText: input.value,
            originalInput: msg,
            outputHtml: `<span style="color:#fca5a5">${esc('Error: ' + err.message)}</span>`,
            metaText: '',
            ran: true,
        };
    } finally {
        btn.disabled = false;
        btn.textContent = 'Send';
    }
}

// ============================================================
// Activate / Deactivate
// ============================================================
async function handleActivateChain() {
    if (state.chain.length === 0) return;
    if (state.isActivating) return;
    if (state.invalidFields.size > 0) {
        showStatus('proposed-status', 'error', 'Fix errors before activating');
        return;
    }

    state.isActivating = true;
    updateActivateButton();
    showStatus('proposed-status', 'info', 'Activating...');

    try {
        let payload;

        if (state.chain.length === 1) {
            payload = {
                policy_class_ref: state.chain[0].classRef,
                config: state.chain[0].config,
                enabled_by: 'ui'
            };
        } else {
            const subPolicies = state.chain.map(item => ({
                class: item.classRef,
                config: item.config,
            }));
            payload = {
                policy_class_ref: MULTI_SERIAL_CLASS_REF,
                config: { policies: subPolicies },
                enabled_by: 'ui'
            };
        }

        const result = await apiCall('/api/admin/policy/set', {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        if (result.success) {
            await loadCurrentPolicy();
            state.chain = [];
            state.expandedChainIndex = -1;
            testState.proposed = {};
            testState.active = {};
            renderAll();
        } else {
            const errMsg = result.error || 'Activation failed';
            if (result.validation_errors && result.validation_errors.length > 0) {
                showStatus('proposed-status', 'error', 'Validation errors — check highlighted fields');
            } else {
                showStatus('proposed-status', 'error', errMsg);
            }
        }
    } catch (err) {
        showStatus('proposed-status', 'error', err.message);
    } finally {
        state.isActivating = false;
        updateActivateButton();
    }
}

async function handleDeactivate() {
    state.isActivating = true;
    try {
        const result = await apiCall('/api/admin/policy/set', {
            method: 'POST',
            body: JSON.stringify({
                policy_class_ref: NOOP_CLASS_REF,
                config: {},
                enabled_by: 'ui'
            })
        });
        if (result.success) {
            await loadCurrentPolicy();
            testState.active = {};
            renderAll();
        } else {
            showStatus('active-status', 'error', result.error || 'Deactivation failed');
        }
    } catch (err) {
        showStatus('active-status', 'error', `Deactivation failed: ${err.message}`);
    } finally {
        state.isActivating = false;
    }
}

function showStatus(containerId, type, message) {
    const el = document.getElementById(containerId);
    if (el) el.innerHTML = `<div class="status-msg ${type}">${esc(message)}</div>`;
}

// ============================================================
// Sub-policy list management (for Alpine.js FormRenderer)
// ============================================================
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

    if (window.Alpine) Alpine.initTree(container);

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

    const safePath = esc(path);
    return `
        <div class="sub-policy-card" data-path="${safePath}" data-index="${index}">
            <div class="sub-policy-card-header">
                <select class="sub-policy-select"
                        x-model="formData.${path}[${index}].class"
                        @change="window.onSubPolicyClassChange('${safePath}', ${index}, $event.target.value)">
                    ${options}
                </select>
                <div class="sub-policy-card-actions">
                    <button type="button" class="btn-move" onclick="window.moveSubPolicy('${safePath}', ${index}, -1)" title="Move up">&uarr;</button>
                    <button type="button" class="btn-move" onclick="window.moveSubPolicy('${safePath}', ${index}, 1)" title="Move down">&darr;</button>
                    <button type="button" class="btn-remove-sub" onclick="window.removeSubPolicy('${safePath}', ${index})" title="Remove">&times;</button>
                </div>
            </div>
            <div class="sub-policy-config" id="sub-policy-config-${safePath}-${index}">
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
        if (window.Alpine) Alpine.initTree(configContainer);
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

window.openDrawer = function(path, index) {
    if (window.Alpine) {
        Alpine.store('drawer').open = true;
        Alpine.store('drawer').path = path;
        Alpine.store('drawer').index = index;
    }
};

window.closeDrawer = function() {
    if (window.Alpine) Alpine.store('drawer').open = false;
};

window.validateJson = function(event) {
    try {
        JSON.parse(event.target.value);
        event.target.classList.remove('invalid');
    } catch {
        event.target.classList.add('invalid');
    }
};

window.onUnionTypeChange = function() {
    // TODO: Reset stale form fields when union type changes
};

// ============================================================
// Render all
// ============================================================
function renderAll() {
    renderAvailable();
    renderProposed();
    renderActive();
}
