// ============================================================
// Policy Configuration — Chain-first UI
// ============================================================

const NOOP_CLASS_REF = 'luthien_proxy.policies.noop_policy:NoOpPolicy';
const DEFAULT_MODEL = 'claude-haiku-4-5-20251001';
const MULTI_SERIAL_CLASS_REF = 'luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy';

// Category display order and labels — server provides `category` per policy.
const CATEGORY_ORDER = ['simple_utilities', 'active_monitoring', 'fun_and_goofy', 'advanced'];
const CATEGORY_LABELS = {
    simple_utilities: 'Simple Utilities',
    active_monitoring: 'Active Monitoring & Editing',
    fun_and_goofy: 'Fun & Goofy',
    advanced: 'Advanced / Debugging',
    internal: 'Internal',
};

// Sub-group labels within a category — server provides optional `group` per policy.
// Multi-select "select all that apply" UI activates when 2+ policies share a group.
const GROUP_LABELS = {
    blocks: 'Blocks',
};

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
        desc: 'Config: model = "claude-haiku-4-5-20251001", threshold = 0.01',
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
    'luthien_proxy.policies.presets.deslop:DeSlopPolicy': {
        desc: null,
        input: 'I\'d be happy to help! Let me delve into this comprehensive topic \u2014 it\'s certainly a pivotal one.',
        output: 'Let me look at this topic, it\'s an important one.',
        testInput: 'I\'m thrilled to share that I\'ve been on an absolutely incredible journey! ' +
            'After delving deep into the comprehensive world of automation \u2014 and I mean DEEP \u2014 ' +
            'I\'ve leveraged cutting-edge tools to streamline my cold outreach pipeline. ' +
            'It\'s been a pivotal paradigm shift that has fundamentally transformed how I facilitate ' +
            'meaningful connections. I\'d be happy to share more insights \u2014 feel free to reach out!',
    },
    'luthien_proxy.policies.presets.no_yapping:NoYappingPolicy': {
        desc: null,
        input: 'Certainly! Great question! Let me walk you through the solution step by step.',
        output: 'Here\'s the solution:',
    },
    'luthien_proxy.policies.presets.no_apologies:NoApologiesPolicy': {
        desc: null,
        input: 'I apologize for the confusion. The correct answer is 42.',
        output: 'The correct answer is 42.',
    },
};

// ============================================================
// State
// ============================================================
const state = {
    policies: [],
    currentPolicy: null,
    chain: [],
    availableModels: [],
    selectedModel: DEFAULT_MODEL,
    isActivating: false,
    cachedCredentials: [],
    credentialSource: 'server',
    showHidden: false,
    expandedChainIndex: -1,
    expandedAvailablePolicy: null,
    collapsedCategories: new Set(['fun_and_goofy', 'advanced']),
    checkedGroupItems: new Set(),
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

const DEFAULT_PROPOSED_REF = 'luthien_proxy.policies.presets.deslop:DeSlopPolicy';

document.addEventListener('DOMContentLoaded', async () => {
    initFilterInput();
    await Promise.all([loadPolicies(), loadCurrentPolicy(), loadModels(), loadGatewaySettings()]);
    // Auto-propose De-Slop if nothing is in the chain yet
    if (state.chain.length === 0) {
        const defaultPolicy = getPolicy(DEFAULT_PROPOSED_REF);
        if (defaultPolicy) {
            state.chain.push({ classRef: DEFAULT_PROPOSED_REF, config: defaultConfigFor(defaultPolicy) });
            state.expandedChainIndex = 0;
        }
    }
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
        // Canonical config API returns { config: [ { name, value, ... }, ... ] }
        const data = await apiCall('/api/admin/config');
        const byName = Object.fromEntries((data.config || []).map(f => [f.name, f.value]));
        document.getElementById('set-inject').checked = Boolean(byName.inject_policy_context);
        document.getElementById('set-dogfood').checked = Boolean(byName.dogfood_mode);
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
        await apiCall(`/api/admin/config/${encodeURIComponent(field)}`, {
            method: 'PUT',
            body: JSON.stringify({ value })
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
// Render: Available column (category accordion)
// ============================================================
function renderAvailable() {
    const filter = document.getElementById('filter-input').value.toLowerCase();
    const list = document.getElementById('available-list');
    list.innerHTML = '';

    // Group by category from server
    const groups = {};
    let hiddenCount = 0;
    for (const p of state.policies) {
        if (filter) {
            const haystack = [p.name, displayName(p), p.short_description, p.description]
                .map(s => (s || '').toLowerCase());
            if (!haystack.some(s => s.includes(filter))) continue;
        }
        const cat = p.category || 'advanced';
        if (cat === 'internal') { hiddenCount++; if (!state.showHidden) continue; }
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(p);
    }

    // Render in order
    const allCats = [...CATEGORY_ORDER];
    if (state.showHidden) allCats.push('internal');
    for (const cat of Object.keys(groups)) { if (!allCats.includes(cat)) allCats.push(cat); }

    for (const cat of allCats) {
        const policies = groups[cat];
        if (!policies || policies.length === 0) continue;
        const isCollapsed = state.collapsedCategories.has(cat);

        const section = document.createElement('div');
        section.className = 'category-section';

        const label = document.createElement('div');
        label.className = 'group-label';
        const labelText = document.createElement('span');
        labelText.textContent = CATEGORY_LABELS[cat] || cat;
        const arrow = document.createElement('span');
        arrow.className = 'category-arrow';
        arrow.textContent = isCollapsed ? '\u25B6' : '\u25BC';
        label.appendChild(labelText);
        label.appendChild(arrow);
        label.onclick = () => toggleCategory(cat);
        section.appendChild(label);

        if (!isCollapsed) {
            // Partition this category's policies into singletons and groups (>=2 sharing `group`).
            // Singletons render in their existing order; group cards render after.
            const groupBuckets = {};
            const singletons = [];
            for (const p of policies) {
                if (p.group) {
                    if (!groupBuckets[p.group]) groupBuckets[p.group] = [];
                    groupBuckets[p.group].push(p);
                } else {
                    singletons.push(p);
                }
            }
            // A group with only one member falls back to a normal singleton card.
            for (const [gKey, gPolicies] of Object.entries(groupBuckets)) {
                if (gPolicies.length < 2) {
                    singletons.push(...gPolicies);
                    delete groupBuckets[gKey];
                }
            }
            for (const p of singletons) renderPolicyCard(p, section);
            for (const [gKey, gPolicies] of Object.entries(groupBuckets)) {
                renderGroupedCard(gKey, gPolicies, section);
            }
        }
        list.appendChild(section);
    }

    if (hiddenCount > 0) {
        const toggle = document.createElement('button');
        toggle.className = 'show-hidden-btn';
        toggle.textContent = state.showHidden ? 'Hide internal policies' : `Show ${hiddenCount} internal policies`;
        toggle.onclick = toggleShowHidden;
        list.appendChild(toggle);
    }

    if (list.children.length === 0 && state.policies.length > 0) {
        const el = document.createElement('div');
        el.className = 'empty-state';
        el.textContent = 'No policies match filter';
        list.appendChild(el);
    }
}

function toggleCategory(cat) {
    if (state.collapsedCategories.has(cat)) state.collapsedCategories.delete(cat);
    else state.collapsedCategories.add(cat);
    renderAvailable();
}

function displayName(p) {
    return p.display_name || p.name;
}

function renderPolicyCard(p, container) {
    const div = document.createElement('div');
    div.className = 'policy-card';
    const inActiveChain = isInActiveChain(p.class_ref);
    if (isCurrentActive(p.class_ref) || inActiveChain) div.className += ' is-active';
    if (state.chain.some(c => c.classRef === p.class_ref)) div.className += ' in-chain';

    const nameEl = document.createElement('div');
    nameEl.className = 'p-name';

    if (isCurrentActive(p.class_ref)) {
        const dot = document.createElement('span');
        dot.className = 'active-dot';
        dot.title = 'Currently active';
        nameEl.appendChild(dot);
    }

    const nameText = document.createElement('span');
    nameText.textContent = displayName(p);
    nameEl.appendChild(nameText);

    // Badges
    if (p.badges && p.badges.length > 0) {
        for (const badge of p.badges) {
            const badgeEl = document.createElement('span');
            badgeEl.className = 'p-badge';
            badgeEl.textContent = badge;
            nameEl.appendChild(badgeEl);
        }
    }

    const addBtn = document.createElement('button');
    addBtn.className = 'p-add-btn';
    addBtn.textContent = '+';
    addBtn.onclick = (e) => addToChain(p.class_ref, e);
    nameEl.appendChild(addBtn);

    div.appendChild(nameEl);

    // Short description
    const descText = p.short_description || p.description || '';
    if (descText) {
        const descEl = document.createElement('div');
        descEl.className = 'p-short-desc';
        descEl.textContent = descText;
        div.appendChild(descEl);
    }

    div.onclick = () => addToChain(p.class_ref);
    container.appendChild(div);
}

// Render a multi-select grouped card: one header + N checkboxes + Add selected button.
// Checked-state lives in state.checkedGroupItems and survives renders until "Add selected" clears it.
function renderGroupedCard(groupKey, policies, container) {
    const div = document.createElement('div');
    div.className = 'policy-card grouped-card';

    const header = document.createElement('div');
    header.className = 'grouped-card-header';
    const headerText = document.createElement('span');
    headerText.textContent = GROUP_LABELS[groupKey] || groupKey;
    header.appendChild(headerText);
    const hint = document.createElement('span');
    hint.className = 'group-hint';
    hint.textContent = '· select multiple to add';
    header.appendChild(hint);
    div.appendChild(header);

    for (const p of policies) {
        const inChain = state.chain.some(c => c.classRef === p.class_ref);
        const row = document.createElement('label');
        row.className = 'group-checkbox-row' + (inChain ? ' in-chain' : '');

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'group-checkbox';
        cb.checked = state.checkedGroupItems.has(p.class_ref);
        cb.onclick = (e) => e.stopPropagation();
        cb.onchange = () => {
            if (cb.checked) state.checkedGroupItems.add(p.class_ref);
            else state.checkedGroupItems.delete(p.class_ref);
            const btn = div.querySelector('.group-add-btn');
            if (btn) btn.disabled = state.checkedGroupItems.size === 0;
        };
        row.appendChild(cb);

        const text = document.createElement('div');
        text.className = 'group-row-text';
        const nameEl = document.createElement('div');
        nameEl.className = 'group-row-name';
        nameEl.textContent = displayName(p);
        text.appendChild(nameEl);
        const descText = p.short_description || p.description || '';
        if (descText) {
            const descEl = document.createElement('div');
            descEl.className = 'group-row-desc';
            descEl.textContent = descText;
            text.appendChild(descEl);
        }
        row.appendChild(text);

        div.appendChild(row);
    }

    const btnRow = document.createElement('div');
    btnRow.className = 'group-add-row';
    const addBtn = document.createElement('button');
    addBtn.className = 'group-add-btn';
    addBtn.textContent = 'Add selected';
    addBtn.disabled = !policies.some(p => state.checkedGroupItems.has(p.class_ref));
    addBtn.onclick = (e) => {
        e.stopPropagation();
        addCheckedGroupItems(policies);
    };
    btnRow.appendChild(addBtn);
    div.appendChild(btnRow);

    container.appendChild(div);
}

function addCheckedGroupItems(policies) {
    const toAdd = policies.filter(p => state.checkedGroupItems.has(p.class_ref));
    if (toAdd.length === 0) return;
    for (const p of toAdd) {
        state.chain.push({ classRef: p.class_ref, config: defaultConfigFor(p) });
    }
    state.expandedChainIndex = state.chain.length - 1;
    state.checkedGroupItems.clear();
    renderAll();
}

// ============================================================
// Render: Proposed column — single-policy-first, chain when >1
// ============================================================
function renderProposed() {
    const col = document.getElementById('col-proposed');
    const hasContent = state.chain.length > 0;
    col.className = 'column' + (hasContent ? ' col-proposed has-content' : '');

    const empty = document.getElementById('proposed-empty');
    const content = document.getElementById('proposed-content');

    if (state.chain.length === 0) {
        empty.style.display = ''; content.style.display = 'none';
        empty.innerHTML = '';
        const emptyEl = document.createElement('div');
        emptyEl.className = 'empty-chain-state';
        const icon = document.createElement('div');
        icon.className = 'empty-chain-icon';
        icon.textContent = '+';
        const title = document.createElement('div');
        title.className = 'empty-chain-title';
        title.textContent = 'Select a Policy';
        const desc = document.createElement('div');
        desc.className = 'empty-chain-desc';
        desc.textContent = 'Click a policy on the left to configure it here.';
        emptyEl.appendChild(icon);
        emptyEl.appendChild(title);
        emptyEl.appendChild(desc);
        empty.appendChild(emptyEl);
        return;
    }

    empty.style.display = 'none'; content.style.display = '';

    if (state.chain.length === 1) {
        renderProposedSingle(content);
    } else {
        renderProposedChain(content);
    }
}

function renderProposedSingle(content) {
    const item = state.chain[0];
    const p = getPolicy(item.classRef);
    if (!p) return;
    const hasConfig = configParamCount(p) > 0;

    content.innerHTML = '';

    const nameEl = document.createElement('div');
    nameEl.className = 'proposed-name';
    nameEl.textContent = displayName(p);
    content.appendChild(nameEl);

    // Badges
    if (p.badges && p.badges.length > 0) {
        const badgeRow = document.createElement('div');
        badgeRow.className = 'proposed-badges';
        for (const badge of p.badges) {
            const badgeEl = document.createElement('span');
            badgeEl.className = 'p-badge p-badge-lg';
            badgeEl.textContent = badge;
            badgeRow.appendChild(badgeEl);
        }
        content.appendChild(badgeRow);
    }

    // Instructions summary (like whiteboard "Instructions for judge LLM" box)
    if (p.instructions_summary) {
        const instrEl = document.createElement('div');
        instrEl.className = 'proposed-instructions';
        const instrLabel = document.createElement('div');
        instrLabel.className = 'proposed-instructions-label';
        instrLabel.textContent = 'Instructions for judge LLM';
        const instrText = document.createElement('div');
        instrText.className = 'proposed-instructions-text';
        instrText.textContent = p.instructions_summary;
        instrEl.appendChild(instrLabel);
        instrEl.appendChild(instrText);
        content.appendChild(instrEl);
    } else {
        const descEl = document.createElement('div');
        descEl.className = 'proposed-desc';
        descEl.textContent = p.description || p.short_description || '';
        content.appendChild(descEl);
    }

    // User alert preview
    if (p.user_alert_template) {
        const alertEl = document.createElement('div');
        alertEl.className = 'proposed-alert-preview';
        const alertLabel = document.createElement('div');
        alertLabel.className = 'alert-preview-label';
        alertLabel.textContent = 'User sees when policy acts:';
        const alertText = document.createElement('div');
        alertText.className = 'alert-preview-text';
        alertText.textContent = p.user_alert_template;
        alertEl.appendChild(alertLabel);
        alertEl.appendChild(alertText);
        content.appendChild(alertEl);
    }

    if (hasConfig) {
        const configContainer = document.createElement('div');
        configContainer.id = 'chain-config-0';
        configContainer.className = 'chain-config-container';
        content.appendChild(configContainer);
    } else {
        const noConfig = document.createElement('div');
        noConfig.className = 'no-config';
        noConfig.textContent = 'No configuration needed';
        content.appendChild(noConfig);
    }

    const removeBtn = document.createElement('button');
    removeBtn.className = 'deactivate-link';
    removeBtn.textContent = 'Remove';
    removeBtn.onclick = () => removeChain(0);
    content.appendChild(removeBtn);

    const statusDiv = document.createElement('div');
    statusDiv.id = 'proposed-status';
    content.appendChild(statusDiv);

    const activateBtn = document.createElement('button');
    activateBtn.className = 'btn-activate';
    activateBtn.id = 'btn-activate';
    activateBtn.textContent = 'Activate';
    activateBtn.onclick = handleActivateChain;
    content.appendChild(activateBtn);

    state.expandedChainIndex = 0;
    if (hasConfig) renderChainItemConfig(0);
}

function renderProposedChain(content) {
    content.innerHTML = '';

    const headerRow = document.createElement('div');
    headerRow.className = 'chain-header-row';
    const chainTitle = document.createElement('span');
    chainTitle.className = 'chain-title';
    chainTitle.textContent = 'Policy Chain';
    const chainCount = document.createElement('span');
    chainCount.className = 'chain-count';
    chainCount.textContent = state.chain.length + ' policies';
    headerRow.appendChild(chainTitle);
    headerRow.appendChild(chainCount);
    content.appendChild(headerRow);

    const ul = document.createElement('ul');
    ul.className = 'chain-list';

    for (let i = 0; i < state.chain.length; i++) {
        const item = state.chain[i];
        const p = getPolicy(item.classRef);
        if (!p) continue;
        const isExpanded = state.expandedChainIndex === i;
        const hasConfig = configParamCount(p) > 0;

        const li = document.createElement('li');
        li.className = 'chain-item' + (isExpanded ? ' expanded' : '');

        const header = document.createElement('div');
        header.className = 'chain-item-header';
        const num = document.createElement('span');
        num.className = 'chain-num';
        num.textContent = i + 1;
        header.appendChild(num);
        const name = document.createElement('span');
        name.className = 'chain-item-name';
        name.textContent = displayName(p);
        name.onclick = ((idx) => () => toggleChainExpand(idx))(i);
        header.appendChild(name);

        const actions = document.createElement('span');
        actions.className = 'chain-actions';
        const upBtn = document.createElement('button');
        upBtn.className = 'chain-btn';
        upBtn.innerHTML = '&uarr;';
        upBtn.disabled = i === 0;
        upBtn.onclick = ((idx) => (e) => { e.stopPropagation(); moveChain(idx, -1); })(i);
        actions.appendChild(upBtn);
        const downBtn = document.createElement('button');
        downBtn.className = 'chain-btn';
        downBtn.innerHTML = '&darr;';
        downBtn.disabled = i === state.chain.length - 1;
        downBtn.onclick = ((idx) => (e) => { e.stopPropagation(); moveChain(idx, 1); })(i);
        actions.appendChild(downBtn);
        const rmBtn = document.createElement('button');
        rmBtn.className = 'chain-btn chain-btn-remove';
        rmBtn.innerHTML = '&times;';
        rmBtn.onclick = ((idx) => (e) => { e.stopPropagation(); removeChain(idx); })(i);
        actions.appendChild(rmBtn);
        header.appendChild(actions);
        li.appendChild(header);

        if (isExpanded) {
            const desc = document.createElement('div');
            desc.className = 'chain-item-desc';
            desc.textContent = p.description || '';
            li.appendChild(desc);
            if (hasConfig) {
                const cfg = document.createElement('div');
                cfg.id = 'chain-config-' + i;
                cfg.className = 'chain-config-container';
                li.appendChild(cfg);
            } else {
                const nc = document.createElement('div');
                nc.className = 'no-config';
                nc.textContent = 'No configuration needed';
                li.appendChild(nc);
            }
        } else if (hasConfig) {
            const hint = document.createElement('div');
            hint.className = 'chain-item-config-hint';
            hint.textContent = 'Click to configure';
            hint.onclick = ((idx) => () => toggleChainExpand(idx))(i);
            li.appendChild(hint);
        }

        ul.appendChild(li);
        if (i < state.chain.length - 1) {
            const divider = document.createElement('div');
            divider.className = 'chain-divider';
            divider.innerHTML = '&darr;';
            ul.appendChild(divider);
        }
    }
    content.appendChild(ul);

    const statusDiv = document.createElement('div');
    statusDiv.id = 'proposed-status';
    content.appendChild(statusDiv);

    const activateBtn = document.createElement('button');
    activateBtn.className = 'btn-activate';
    activateBtn.id = 'btn-activate';
    activateBtn.textContent = 'Activate Chain (' + state.chain.length + ' policies)';
    activateBtn.onclick = handleActivateChain;
    content.appendChild(activateBtn);

    for (let i = 0; i < state.chain.length; i++) {
        if (state.expandedChainIndex === i) renderChainItemConfig(i);
    }
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
    if (!window.FormRenderer) return;

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
}

function updateChainConfig(index, data) {
    if (state.chain[index]) {
        state.chain[index].config = data;
        updateActivateButton();
    }
}

function updateActivateButton() {
    const btn = document.getElementById('btn-activate');
    if (!btn) return;

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

    const name = isChain ? 'Chain' : (p ? displayName(p) : cp.policy);
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
            const subName = subPolicy ? displayName(subPolicy) : (classRef || 'Unknown').split(':').pop();
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

    body.innerHTML = html;
    body.appendChild(renderTestSection('active'));
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
    const container = document.createElement('div');
    container.className = 'test-section';

    const title = document.createElement('div');
    title.className = 'test-section-title';
    title.textContent = 'Test this policy with a prompt';
    container.appendChild(title);

    // Model row (collapsed by default)
    const advancedToggle = document.createElement('details');
    advancedToggle.className = 'test-advanced';
    const summary = document.createElement('summary');
    summary.className = 'test-advanced-toggle';
    summary.textContent = 'Advanced options';
    advancedToggle.appendChild(summary);

    const advancedBody = document.createElement('div');
    advancedBody.className = 'test-advanced-body';

    // Model
    const modelRow = document.createElement('div');
    modelRow.className = 'test-model-row';
    const modelInput = document.createElement('input');
    modelInput.type = 'text';
    modelInput.className = 'test-model-input';
    modelInput.id = 'test-model-' + side;
    modelInput.placeholder = 'Model...';
    modelInput.value = state.selectedModel;
    modelInput.setAttribute('list', 'model-list-' + side);
    modelRow.appendChild(modelInput);
    const datalist = document.createElement('datalist');
    datalist.id = 'model-list-' + side;
    for (const m of state.availableModels) {
        const opt = document.createElement('option');
        opt.value = m;
        datalist.appendChild(opt);
    }
    modelRow.appendChild(datalist);
    advancedBody.appendChild(modelRow);

    // Mock checkbox
    const mockRow = document.createElement('div');
    mockRow.className = 'test-mock-row';
    const mockLabel = document.createElement('label');
    mockLabel.className = 'test-mock-label';
    const mockCb = document.createElement('input');
    mockCb.type = 'checkbox';
    mockCb.id = 'test-mock-' + side;
    mockCb.className = 'test-mock-checkbox';
    mockLabel.appendChild(mockCb);
    const mockText = document.createElement('span');
    mockText.textContent = 'Mock';
    mockLabel.appendChild(mockText);
    const mockInfo = document.createElement('span');
    mockInfo.className = 'test-mock-info';
    mockInfo.title = 'Skip the real LLM call. Echoes your message back (no API credits).';
    mockInfo.textContent = '\u2139';
    mockLabel.appendChild(mockInfo);
    mockRow.appendChild(mockLabel);
    advancedBody.appendChild(mockRow);

    // Credential override
    const credRow = document.createElement('div');
    credRow.className = 'test-cred-row';
    const credLabel = document.createElement('label');
    credLabel.className = 'test-cred-label';
    credLabel.textContent = 'Credentials:';
    credRow.appendChild(credLabel);
    const credSelect = document.createElement('select');
    credSelect.className = 'test-cred-select';
    credSelect.id = 'cred-select-' + side;
    credSelect.onchange = function() { onCredSourceChange(side, this.value); };
    const opt1 = document.createElement('option');
    opt1.value = 'server';
    opt1.textContent = 'Server API Key';
    if (state.credentialSource === 'server') opt1.selected = true;
    credSelect.appendChild(opt1);
    const opt2 = document.createElement('option');
    opt2.value = 'custom';
    opt2.textContent = 'Enter API key...';
    if (state.credentialSource === 'custom') opt2.selected = true;
    credSelect.appendChild(opt2);
    credRow.appendChild(credSelect);
    advancedBody.appendChild(credRow);

    if (state.credentialSource === 'custom') {
        const credCustom = document.createElement('div');
        credCustom.className = 'test-cred-custom';
        const credInput = document.createElement('input');
        credInput.type = 'password';
        credInput.className = 'credential-input';
        credInput.id = 'cred-input-' + side;
        credInput.placeholder = 'sk-ant-...';
        credCustom.appendChild(credInput);
        advancedBody.appendChild(credCustom);
    }

    advancedToggle.appendChild(advancedBody);
    container.appendChild(advancedToggle);

    // Test input row
    const inputRow = document.createElement('div');
    inputRow.className = 'test-input-row';
    const textarea = document.createElement('textarea');
    textarea.className = 'test-textarea';
    textarea.id = 'test-input-' + side;
    textarea.placeholder = 'Enter test message...';
    textarea.rows = 3;
    // Pre-fill with example text if the active policy declares one in EXAMPLES.
    const activeRef = state.currentPolicy ? state.currentPolicy.class_ref : '';
    const example = EXAMPLES[activeRef];
    if (example && example.testInput) {
        textarea.value = example.testInput;
    }
    textarea.onkeydown = function(event) {
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); runTest(side); }
    };
    inputRow.appendChild(textarea);
    const testBtn = document.createElement('button');
    testBtn.className = 'test-run-btn';
    testBtn.id = 'test-btn-' + side;
    testBtn.textContent = 'TEST';
    testBtn.onclick = function() { runTest(side); };
    inputRow.appendChild(testBtn);
    container.appendChild(inputRow);

    // Results — Before / After
    const results = document.createElement('div');
    results.className = 'test-results';
    results.id = 'test-results-' + side;
    const beforeDiv = document.createElement('div');
    const beforeLabel = document.createElement('div');
    beforeLabel.className = 'test-box-label';
    beforeLabel.textContent = 'Before';
    beforeDiv.appendChild(beforeLabel);
    const beforeBox = document.createElement('div');
    beforeBox.className = 'test-box test-box-input';
    beforeBox.id = 'test-box-in-' + side;
    beforeDiv.appendChild(beforeBox);
    results.appendChild(beforeDiv);
    const afterDiv = document.createElement('div');
    const afterLabel = document.createElement('div');
    afterLabel.className = 'test-box-label';
    afterLabel.textContent = 'After';
    afterDiv.appendChild(afterLabel);
    const afterBox = document.createElement('div');
    afterBox.className = 'test-box test-box-output';
    afterBox.id = 'test-box-out-' + side;
    afterDiv.appendChild(afterBox);
    results.appendChild(afterDiv);
    container.appendChild(results);

    const meta = document.createElement('div');
    meta.className = 'test-meta';
    meta.id = 'test-meta-' + side;
    container.appendChild(meta);

    return container;
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
            capture_before: true,
        };
        if (state.credentialSource === 'custom') {
            const credInput = document.getElementById('cred-input-' + side);
            const key = credInput ? credInput.value.trim() : '';
            if (key) testPayload.api_key = key;
        }
        const result = await apiCall('/api/admin/test/chat', {
            method: 'POST',
            body: JSON.stringify(testPayload)
        });

        if (!result.success) throw new Error(result.error || 'Request failed');

        const content = result.content || '(empty response)';
        const beforeContent = result.before_content || content;
        boxIn.textContent = beforeContent;
        boxOut.textContent = content;
        boxOut.style.color = '';

        let metaText = 'Model: ' + (result.model || model);
        if (result.usage) {
            metaText += ' | ' + result.usage.prompt_tokens + ' in / ' + result.usage.completion_tokens + ' out';
        }
        meta.textContent = metaText;

        testState[side] = {
            inputText: input.value,
            originalInput: beforeContent,
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
