function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

function conversationViewer() {
    return {
        conversationId: '',
        turns: [],
        rawEvents: {},
        stats: { turns: 0, interventions: 0, models: [], events: 0 },
        connection: 'connecting',
        autoScroll: true,
        lastUpdated: '',
        evtSource: null,
        pollTimer: null,
        refreshTimer: null,
        expandedRaw: {},
        expandedContent: {},
        expandedDiffs: {},
        renderedCallIds: new Set(),
        turnFingerprints: {},

        init() {
            const pathParts = window.location.pathname.split('/');
            this.conversationId = decodeURIComponent(pathParts[pathParts.length - 1]);
            this.setupEventDelegation();
            this.loadInitial();
            this.connectSSE();
        },

        setupEventDelegation() {
            const container = document.getElementById('conversation-container');
            container.addEventListener('click', (e) => {
                // Toggle preflight turn expansion
                const preflightHeader = e.target.closest('.preflight .turn-header');
                if (preflightHeader) {
                    preflightHeader.closest('.turn').classList.toggle('expanded');
                    return;
                }

                const target = e.target.closest('[data-event-timeline]');
                if (target) {
                    const callId = target.getAttribute('data-event-timeline');
                    const list = target.parentElement.querySelector('.event-list');
                    if (list) {
                        list.classList.toggle('visible');
                        target.classList.toggle('open');
                    }
                    return;
                }

                const diffBtn = e.target.closest('[data-diff-toggle]');
                if (diffBtn) {
                    const diffId = diffBtn.getAttribute('data-diff-toggle');
                    const panel = document.getElementById(diffId);
                    if (panel) {
                        panel.classList.toggle('visible');
                        diffBtn.classList.toggle('open');
                    }
                    return;
                }

                const expandBtn = e.target.closest('[data-expand-btn]');
                if (expandBtn) {
                    const contentId = expandBtn.getAttribute('data-expand-btn');
                    const el = document.getElementById(contentId);
                    if (el) {
                        const expanded = el.classList.contains('expanded');
                        el.classList.toggle('expanded');
                        expandBtn.textContent = expanded ? 'Show more' : 'Show less';
                    }
                    return;
                }

                const rawBtn = e.target.closest('[data-toggle-raw]');
                if (rawBtn) {
                    const eventKey = rawBtn.getAttribute('data-toggle-raw');
                    const el = document.getElementById(`raw-${eventKey}`);
                    if (el) {
                        el.classList.toggle('visible');
                        rawBtn.textContent = el.classList.contains('visible') ? 'Hide' : 'Raw';
                    }
                    return;
                }
            });
        },

        async loadInitial() {
            try {
                const resp = await fetch(
                    `/api/history/sessions/${encodeURIComponent(this.conversationId)}`,
                    { headers: { 'Accept': 'application/json' } }
                );

                if (!resp.ok) {
                    if (resp.status === 403) {
                        window.location.href = '/login?error=required&next=' +
                            encodeURIComponent(window.location.pathname);
                        return;
                    }
                    if (resp.status === 404) throw new Error('Conversation not found');
                    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
                }

                const data = await resp.json();
                this.processTurns(data);
                this.updateStats(data);
                this.updateTimestamp();
                this.renderTurns();
                this.$nextTick(() => this.autoScrollToBottom());
            } catch (err) {
                this.showError(`Failed to load: ${err.message}`);
            }
        },

        connectSSE() {
            if (this.evtSource) {
                this.evtSource.close();
            }

            try {
                this.evtSource = new EventSource('/api/activity/stream');

                this.evtSource.onopen = () => {
                    this.connection = 'connected';
                };

                this.evtSource.onmessage = (evt) => {
                    try {
                        const data = JSON.parse(evt.data);
                        const eventSessionId = data.data?.session_id || data.session_id;
                        if (eventSessionId === this.conversationId) {
                            this.handleSSEEvent(data);
                        }
                    } catch (e) {
                        console.error('Failed to parse SSE event:', e);
                    }
                };

                this.evtSource.onerror = () => {
                    this.connection = 'reconnecting';
                    if (this.evtSource.readyState === EventSource.CLOSED) {
                        this.evtSource.close();
                        setTimeout(() => this.connectSSE(), 3000);
                    }
                };
            } catch (err) {
                console.error('Failed to connect SSE:', err);
                this.connection = 'reconnecting';
                setTimeout(() => this.connectSSE(), 3000);
            }
        },

        handleSSEEvent(event) {
            if (!event.event_type) return;

            const eventType = event.event_type.toLowerCase();
            const callId = event.call_id || event.id;

            if (!this.rawEvents[callId]) {
                this.rawEvents[callId] = [];
            }

            this.rawEvents[callId].push({
                type: eventType,
                timestamp: event.timestamp || new Date().toISOString(),
                data: event
            });

            this.stats.events++;

            const shouldRefresh = eventType.includes('request_recorded') ||
                                eventType.includes('response_recorded') ||
                                eventType.includes('policy.');

            if (shouldRefresh) {
                this.debouncedRefresh();
            }
        },

        debouncedRefresh() {
            if (this.refreshTimer) clearTimeout(this.refreshTimer);
            this.refreshTimer = setTimeout(() => this.refreshTurns(), 1000);
        },

        async refreshTurns() {
            try {
                const resp = await fetch(
                    `/api/history/sessions/${encodeURIComponent(this.conversationId)}`,
                    { headers: { 'Accept': 'application/json' } }
                );
                if (!resp.ok) return;
                const data = await resp.json();
                const rawTurns = data.turns || [];
                const newTurns = this.presentTurns(rawTurns);
                this.turns = newTurns;

                this.updateStats(data);
                this.updateTimestamp();

                const container = document.getElementById('conversation-container');

                // Remove empty/loading state if present
                const emptyState = container.querySelector('.empty-state, .loading-state');
                if (emptyState) emptyState.remove();

                const savedState = this.snapshotExpandState();

                // Update existing turns only if their server data changed
                // (e.g. response arrived, late policy annotation).
                // Fingerprint raw server data, not derived presentation state.
                for (let i = 0; i < newTurns.length; i++) {
                    const turn = newTurns[i];
                    const fp = JSON.stringify(rawTurns[i]);
                    if (this.renderedCallIds.has(turn.call_id)) {
                        if (fp !== this.turnFingerprints[turn.call_id]) {
                            const existing = container.querySelector(`[data-call-id="${CSS.escape(turn.call_id)}"]`);
                            if (existing) {
                                existing.outerHTML = this.renderTurn(turn, i + 1);
                            }
                            this.turnFingerprints[turn.call_id] = fp;
                        }
                    } else {
                        // New turn — append
                        this.renderedCallIds.add(turn.call_id);
                        this.turnFingerprints[turn.call_id] = fp;
                        const html = this.renderTurn(turn, i + 1);
                        container.insertAdjacentHTML('beforeend', html);
                        const newEl = container.lastElementChild;
                        if (newEl) newEl.classList.add('new-turn');
                    }
                }

                this.restoreExpandState(savedState);
                this.autoScrollToBottom();
            } catch (err) {
                console.error('Failed to refresh turns:', err);
            }
        },

        processTurns(data) {
            this.turns = this.presentTurns(data.turns || []);
        },

        // Presentation pipeline: classify preflight turns and compute
        // display messages (dedup) entirely on the client side.
        //
        // The API sends the full conversation history on every request:
        //   Turn 1: [user₀]
        //   Turn 2: [user₀, assistant₁, user₂]
        //   Turn 3: [user₀, assistant₁, user₂, tool_call₂, tool_result₂, user₃]
        //
        // user₀ (the initial message with all preamble) is re-sent identically
        // every turn. New content appears at the end, after the previous turn's
        // messages. So for turn N, display = request_messages.slice(prevCount).
        // Preflight turns are excluded from the count so they don't disrupt the
        // sequence.
        presentTurns(rawTurns) {
            let prevRealMsgCount = 0;

            return rawTurns.map(turn => {
                const isPreflight = this.classifyPreflight(turn);

                let displayMessages;
                if (isPreflight) {
                    displayMessages = turn.request_messages;
                } else {
                    displayMessages = turn.request_messages.slice(prevRealMsgCount);
                    prevRealMsgCount = turn.request_messages.length;
                }

                return { ...turn, _isPreflight: isPreflight, _displayMessages: displayMessages };
            });
        },

        // Classify non-conversational preflight turns using structural
        // request params (not response content heuristics).
        //   - Quota probe: max_tokens === 1
        //   - Title generation: output_config.format.type === "json_schema"
        // These can appear at any position in the session.
        classifyPreflight(turn) {
            const params = turn.request_params || {};
            if (params.max_tokens === 1) return true;
            if (params.output_config?.format?.type === 'json_schema') return true;
            return false;
        },

        updateStats(data) {
            const realTurns = this.turns.filter(t => !t._isPreflight);
            this.stats.turns = realTurns.length;
            this.stats.interventions = data.total_policy_interventions || 0;
            this.stats.models = [...new Set(data.models_used || [])];
            this.stats.events = Object.values(this.rawEvents).reduce((sum, events) => sum + events.length, 0);
        },

        updateTimestamp() {
            const now = new Date();
            this.lastUpdated = `Updated ${now.toLocaleTimeString()}`;
        },

        autoScrollToBottom() {
            if (this.autoScroll) {
                const container = document.getElementById('conversation-container');
                if (container && container.lastElementChild) {
                    container.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }
        },

        toggleAutoScroll() {
            this.autoScroll = !this.autoScroll;
            if (this.autoScroll) {
                this.autoScrollToBottom();
            }
        },

        connectionText() {
            if (this.connection === 'connected') return 'Connected via SSE';
            if (this.connection === 'reconnecting') return 'Reconnecting...';
            if (!this.autoScroll) return 'Paused';
            return 'Connecting...';
        },

        showError(msg) {
            const container = document.getElementById('conversation-container');
            container.innerHTML = `<div class="error-state">${escapeHtml(msg)}</div>`;
        },

        formatTime(iso) {
            if (!iso) return '';
            const date = new Date(iso);
            return date.toLocaleTimeString();
        },

        snapshotExpandState() {
            const state = { visible: [], expanded: [], open: [] };
            document.querySelectorAll('.visible[id]').forEach(el => state.visible.push(el.id));
            document.querySelectorAll('.expanded[id]').forEach(el => state.expanded.push(el.id));
            document.querySelectorAll('.open[data-event-timeline]').forEach(el => {
                state.open.push(el.getAttribute('data-event-timeline'));
            });
            document.querySelectorAll('.open[data-diff-toggle]').forEach(el => {
                state.open.push('diff:' + el.getAttribute('data-diff-toggle'));
            });
            return state;
        },

        restoreExpandState(state) {
            state.visible.forEach(id => {
                const el = document.getElementById(id);
                if (el) el.classList.add('visible');
            });
            state.expanded.forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.classList.add('expanded');
                    const btn = document.querySelector(`[data-expand-btn="${id}"]`);
                    if (btn) btn.textContent = 'Show less';
                }
            });
            state.open.forEach(key => {
                if (key.startsWith('diff:')) {
                    const diffId = key.slice(5);
                    const panel = document.getElementById(diffId);
                    const btn = document.querySelector(`[data-diff-toggle="${diffId}"]`);
                    if (panel) panel.classList.add('visible');
                    if (btn) btn.classList.add('open');
                } else {
                    const btn = document.querySelector(`[data-event-timeline="${key}"]`);
                    if (btn) {
                        btn.classList.add('open');
                        const list = btn.parentElement?.querySelector('.event-list');
                        if (list) list.classList.add('visible');
                    }
                }
            });
        },

        renderTurns() {
            const container = document.getElementById('conversation-container');

            if (!this.turns || this.turns.length === 0) {
                container.innerHTML = '<div class="empty-state">Waiting for conversation events...</div>';
                return;
            }

            this.renderedCallIds.clear();
            this.turnFingerprints = {};
            for (const turn of this.turns) {
                this.renderedCallIds.add(turn.call_id);
                this.turnFingerprints[turn.call_id] = JSON.stringify(turn);
            }

            const savedState = this.snapshotExpandState();
            container.innerHTML = this.turns.map((turn, i) => this.renderTurn(turn, i + 1)).join('');
            this.restoreExpandState(savedState);
        },

        renderTurn(turn, number) {
            const hasIntervention = turn.had_policy_intervention;
            const isPreflight = turn._isPreflight;
            const classes = ['turn', `turn-${number - 1}`];
            if (hasIntervention) classes.push('has-intervention');
            if (isPreflight) classes.push('preflight');

            const callId = escapeHtml(turn.call_id);
            const displayMessages = turn._displayMessages || turn.request_messages || [];
            const responseMessages = turn.response_messages || [];

            // Build unified message list pairing tool calls with their results.
            // Tool results (from request) match tool calls (from request or response)
            // via tool_call_id. We also skip request tool_calls that duplicate
            // a response tool_call from this same turn (the API re-sends them).
            const toolResultsByCallId = {};
            for (const m of displayMessages) {
                if (m.message_type === 'tool_result' && m.tool_call_id) {
                    toolResultsByCallId[m.tool_call_id] = m;
                }
            }

            // Track response tool_call IDs to suppress duplicates from request
            const responseToolCallIds = new Set();
            for (const m of responseMessages) {
                if (m.message_type === 'tool_call' && m.tool_call_id) {
                    responseToolCallIds.add(m.tool_call_id);
                }
            }

            const orderedMessages = [];
            const usedResultIds = new Set();

            // Request messages: skip tool_results (paired later) and
            // tool_calls that also appear in the response (duplicates)
            for (const m of displayMessages) {
                if (m.message_type === 'tool_result') continue;
                if (m.message_type === 'tool_call' && responseToolCallIds.has(m.tool_call_id)) continue;
                orderedMessages.push(m);
                // If this request tool_call has a result, pair it
                if (m.message_type === 'tool_call' && toolResultsByCallId[m.tool_call_id]) {
                    orderedMessages.push(toolResultsByCallId[m.tool_call_id]);
                    usedResultIds.add(m.tool_call_id);
                }
            }

            // Response messages with paired results
            for (const m of responseMessages) {
                orderedMessages.push(m);
                if (m.message_type === 'tool_call' && toolResultsByCallId[m.tool_call_id]) {
                    orderedMessages.push(toolResultsByCallId[m.tool_call_id]);
                    usedResultIds.add(m.tool_call_id);
                }
            }

            // Any orphaned tool results
            for (const id in toolResultsByCallId) {
                if (!usedResultIds.has(id)) {
                    orderedMessages.push(toolResultsByCallId[id]);
                }
            }

            const messagesHtml = orderedMessages.map((m, mi) =>
                this.renderMessage(m, `${callId}-m${mi}`)
            ).join('');

            let diffHtml = '';
            if (turn.request_was_modified || turn.response_was_modified) {
                diffHtml = this.renderDiffSection(turn);
            }

            let annotationsHtml = '';
            if (turn.annotations && turn.annotations.length > 0) {
                annotationsHtml = `
                    <div class="messages">
                        ${turn.annotations.map(a => `
                            <div class="policy-event">
                                <span class="policy-event-label">${escapeHtml(a.policy_name)}:</span>
                                ${escapeHtml(a.summary)}
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            let eventTimelineHtml = '';
            const events = this.rawEvents[callId] || [];
            if (events.length > 0) {
                const eventsHtml = events.map((evt, idx) => {
                    const eventKey = `${callId}-${idx}`;
                    return `
                        <div class="event-item">
                            <span class="event-timestamp">${this.formatTime(evt.timestamp)}</span>
                            <span class="event-type">${escapeHtml(evt.type)}</span>
                            <button class="event-raw-toggle" data-toggle-raw="${eventKey}">Raw</button>
                            <div class="event-raw-data" id="raw-${eventKey}">
                                <pre>${escapeHtml(JSON.stringify(evt.data, null, 2))}</pre>
                            </div>
                        </div>
                    `;
                }).join('');

                eventTimelineHtml = `
                    <div class="event-timeline">
                        <button class="event-timeline-toggle" data-event-timeline="${callId}">
                            <span class="arrow">▶</span>
                            Raw Events (${events.length})
                        </button>
                        <div class="event-list">
                            ${eventsHtml}
                        </div>
                    </div>
                `;
            }

            return `
                <div class="${classes.join(' ')}" data-call-id="${callId}">
                    <div class="turn-header">
                        <div class="turn-label">
                            <span class="turn-number">Turn ${number}</span>
                            ${turn.model ? `<span class="turn-model">${escapeHtml(turn.model)}</span>` : ''}
                        </div>
                        ${isPreflight ? '<span class="preflight-badge">Preflight</span>' : ''}
                        ${hasIntervention ? '<span class="intervention-badge">Policy Modified</span>' : ''}
                    </div>
                    <div class="turn-body">
                        <div class="messages">
                            ${messagesHtml}
                        </div>
                        ${eventTimelineHtml}
                        ${diffHtml}
                        ${annotationsHtml}
                    </div>
                </div>
            `;
        },

        renderMessage(msg, stableId) {
            const typeClass = msg.message_type.toLowerCase();
            const typeLabel = {
                'system': 'System', 'user': 'User', 'assistant': 'Assistant',
                'tool_call': 'Tool Call', 'tool_result': 'Tool Result'
            }[typeClass] || msg.message_type;

            let headerExtra = '';
            if (msg.message_type === 'tool_call' && msg.tool_name) {
                headerExtra += `<span class="tool-name">${escapeHtml(msg.tool_name)}</span>`;
            }
            if (msg.tool_call_id) {
                headerExtra += `<span class="tool-call-id">${escapeHtml(msg.tool_call_id)}</span>`;
            }

            const content = msg.content || '';
            const contentId = `c-${stableId}`;

            // Tool calls: show only the pretty-formatted input, not raw content
            if (msg.message_type === 'tool_call' && msg.tool_input) {
                return `
                    <div class="message ${typeClass}">
                        <div class="message-header">
                            <span class="message-type">${typeLabel}</span>
                            ${headerExtra}
                        </div>
                        <div class="tool-input">
                            <pre>${escapeHtml(JSON.stringify(msg.tool_input, null, 2))}</pre>
                        </div>
                    </div>
                `;
            }

            // Tool results: code-style block
            if (msg.message_type === 'tool_result') {
                const shouldTruncate = content.length > 800;
                const expandBtn = shouldTruncate
                    ? `<button class="expand-btn" data-expand-btn="${contentId}">Show more</button>`
                    : '';
                const errorClass = msg.is_error ? ' tool-error' : '';
                const errorBadge = msg.is_error ? '<span class="error-badge">Error</span>' : '';
                return `
                    <div class="message ${typeClass}${errorClass}" ${msg.tool_call_id ? `data-tool-call-id="${escapeHtml(msg.tool_call_id)}"` : ''}>
                        <div class="message-header">
                            <span class="message-type">${typeLabel}</span>
                            ${errorBadge}
                            ${headerExtra}
                        </div>
                        <div class="tool-result-content ${shouldTruncate ? 'truncated' : ''}" id="${contentId}"><pre>${escapeHtml(content)}</pre></div>
                        ${expandBtn}
                    </div>
                `;
            }

            const renderedContent = this.renderContentWithTags(content, contentId);

            return `
                <div class="message ${typeClass}">
                    <div class="message-header">
                        <span class="message-type">${typeLabel}</span>
                        ${headerExtra}
                    </div>
                    ${renderedContent}
                </div>
            `;
        },

        renderContentWithTags(content, contentId) {
            const TAG_LABELS = {
                'system-reminder': 'System Reminder',
                'policy-context': 'Policy Context',
                'local-command-caveat': 'Local Command',
                'bash-input': 'Shell Command',
                'bash-stdout': 'Shell Output',
                'bash-stderr': 'Shell Error',
            };
            const TAG_CLASSES = {
                'system-reminder': 'system-reminder',
                'policy-context': 'policy-context',
                'bash-input': 'bash-output',
                'bash-stdout': 'bash-output',
                'bash-stderr': 'bash-output',
            };

            // Known wrapper tags are flat (never nested within themselves)
            // so a simple regex is reliable here.
            const tagNames = Object.keys(TAG_LABELS).map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
            const tagPattern = new RegExp(
                '<(' + tagNames.join('|') + ')(?:\\s[^>]*)?>([\\s\\S]*?)</\\1>',
                'g'
            );

            const parts = [];
            let lastIndex = 0;
            let match;

            while ((match = tagPattern.exec(content)) !== null) {
                if (match.index > lastIndex) {
                    const before = content.slice(lastIndex, match.index).trim();
                    if (before) parts.push({ type: 'text', content: before });
                }
                parts.push({ type: 'tag', tagName: match[1], content: match[2].trim() });
                lastIndex = match.index + match[0].length;
            }

            if (lastIndex < content.length) {
                const remaining = content.slice(lastIndex).trim();
                if (remaining) parts.push({ type: 'text', content: remaining });
            }

            // If no tags found, fall back to plain rendering
            if (parts.length === 0 || (parts.length === 1 && parts[0].type === 'text')) {
                return this._renderPlainContent(content, contentId);
            }

            return parts.map((part, i) => {
                if (part.type === 'text') {
                    const shouldTruncate = part.content.length > 800;
                    const partId = `${contentId}-p${i}`;
                    const expandBtn = shouldTruncate
                        ? `<button class="expand-btn" data-expand-btn="${partId}">Show more</button>`
                        : '';
                    return `
                        <div class="${shouldTruncate ? 'message-content truncated' : 'message-content'}" id="${partId}">${escapeHtml(part.content)}</div>
                        ${expandBtn}
                    `;
                }

                const label = TAG_LABELS[part.tagName] || part.tagName;
                const cssClass = TAG_CLASSES[part.tagName] || '';
                return `
                    <details class="tagged-section ${cssClass}">
                        <summary><span class="tag-label">${escapeHtml(label)}</span></summary>
                        <div class="tagged-content">${escapeHtml(part.content)}</div>
                    </details>
                `;
            }).join('');
        },

        _renderPlainContent(content, contentId) {
            const shouldTruncate = content.length > 800;
            const expandBtn = shouldTruncate
                ? `<button class="expand-btn" data-expand-btn="${contentId}">Show more</button>`
                : '';
            return `
                <div class="${shouldTruncate ? 'message-content truncated' : 'message-content'}" id="${contentId}">${escapeHtml(content)}</div>
                ${expandBtn}
            `;
        },

        renderDiffSection(turn) {
            const diffId = `diff-${escapeHtml(turn.call_id)}`;

            let requestDiffHtml = '';
            if (turn.request_was_modified && turn.original_request_messages) {
                requestDiffHtml = this.renderDiffPanels(
                    'Request',
                    turn.original_request_messages,
                    turn.request_messages
                );
            }

            let responseDiffHtml = '';
            if (turn.response_was_modified && turn.original_response_messages) {
                responseDiffHtml = this.renderDiffPanels(
                    'Response',
                    turn.original_response_messages,
                    turn.response_messages
                );
            }

            return `
                <div class="diff-section">
                    <button class="diff-toggle" data-diff-toggle="${diffId}">
                        <span class="arrow">▶</span>
                        View Policy Divergence
                    </button>
                    <div class="diff-panels" id="${diffId}">
                        ${requestDiffHtml}
                        ${responseDiffHtml}
                    </div>
                </div>
            `;
        },

        renderDiffPanels(label, originalMsgs, finalMsgs) {
            const maxLen = Math.max(originalMsgs.length, finalMsgs.length);

            let origContent = '';
            let finalContent = '';

            for (let i = 0; i < maxLen; i++) {
                const origMsg = originalMsgs[i];
                const finalMsg = finalMsgs[i];

                const origText = origMsg ? (origMsg.content || '') : '';
                const finalText = finalMsg ? (finalMsg.content || '') : '';
                const changed = origText !== finalText;

                const role = (origMsg && origMsg.message_type) || (finalMsg && finalMsg.message_type) || 'unknown';

                origContent += `
                    <div class="diff-message ${changed ? 'changed' : ''}">
                        <div class="diff-message-role">${escapeHtml(role)}</div>
                        <div class="diff-message-content">${escapeHtml(origText || '(empty)')}</div>
                    </div>
                `;

                finalContent += `
                    <div class="diff-message ${changed ? 'changed' : ''}">
                        <div class="diff-message-role">${escapeHtml(role)}</div>
                        <div class="diff-message-content">${escapeHtml(finalText || '(empty)')}</div>
                    </div>
                `;
            }

            return `
                <div class="diff-panel">
                    <div class="diff-panel-header original">Original ${label}</div>
                    <div class="diff-panel-body">${origContent}</div>
                </div>
                <div class="diff-panel">
                    <div class="diff-panel-header final">Final ${label} (sent to LLM)</div>
                    <div class="diff-panel-body">${finalContent}</div>
                </div>
            `;
        }
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('conversationViewer', conversationViewer);
});
