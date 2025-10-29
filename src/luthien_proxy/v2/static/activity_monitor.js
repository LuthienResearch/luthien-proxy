// Activity Monitor JavaScript
// Real-time event stream display with filtering

let eventSource = null;
let eventCount = 0;
let allEvents = [];

const statusEl = document.getElementById('status');
const statusTextEl = document.getElementById('status-text');
const eventFeedEl = document.getElementById('event-feed');
const clearBtn = document.getElementById('clear-btn');
const reconnectBtn = document.getElementById('reconnect-btn');
const filterCallId = document.getElementById('filter-call-id');
const filterModel = document.getElementById('filter-model');
const eventTypeButton = document.getElementById('event-type-button');
const eventTypeDropdown = document.getElementById('event-type-dropdown');
const eventTypeLabel = document.getElementById('event-type-label');

// Track selected event types (all selected by default)
let selectedEventTypes = new Set([
    'gateway.request_received',
    'gateway.request_sent',
    'gateway.response_received',
    'gateway.response_sent',
    'streaming.chunk_received',
    'streaming.chunk_sent',
    'streaming.original_complete',
    'streaming.transformed_complete',
    'policy_event',
    'transaction.request_recorded',
    'transaction.streaming_response_recorded',
    'transaction.non_streaming_response_recorded',
]);

function updateStatus(connected) {
    if (connected) {
        statusEl.className = 'status connected';
        statusTextEl.textContent = `Connected (${eventCount} events)`;
    } else {
        statusEl.className = 'status disconnected';
        statusTextEl.textContent = 'Disconnected';
    }
}

function formatTime(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        fractionalSecondDigits: 3
    });
}

function matchesFilters(event) {
    // Filter by call_id
    const callIdFilter = filterCallId.value.trim().toLowerCase();
    if (callIdFilter && !event.call_id.toLowerCase().includes(callIdFilter)) {
        return false;
    }

    // Filter by model
    const modelFilter = filterModel.value.trim().toLowerCase();
    if (modelFilter) {
        const eventModel = (event.data?.model || event.model || '').toLowerCase();
        if (!eventModel.includes(modelFilter)) {
            return false;
        }
    }

    // Filter by event type - match exact type or prefix
    const eventType = event.event_type;
    const typeMatches = Array.from(selectedEventTypes).some(selected => {
        if (eventType === selected) return true;
        // "policy_event" matches "policy.*"
        if (selected === 'policy_event' && eventType.startsWith('policy.')) return true;
        return false;
    });
    if (!typeMatches) return false;

    return true;
}

function updateEventTypeLabel() {
    const selectedCount = selectedEventTypes.size;
    const totalCount = eventTypeDropdown.querySelectorAll('input[type="checkbox"]').length;

    if (selectedCount === 0) {
        eventTypeLabel.textContent = 'No Events';
    } else if (selectedCount === totalCount) {
        eventTypeLabel.textContent = 'All Events';
    } else {
        eventTypeLabel.textContent = `${selectedCount} Selected`;
    }
}

function applyFilters() {
    const events = eventFeedEl.querySelectorAll('.event');
    let visibleCount = 0;

    events.forEach((eventEl, idx) => {
        const event = allEvents[allEvents.length - 1 - idx]; // Events are prepended, so reverse index
        if (event && matchesFilters(event)) {
            eventEl.classList.remove('filtered');
            visibleCount++;
        } else {
            eventEl.classList.add('filtered');
        }
    });

    return visibleCount;
}

function renderEvent(event) {
    const eventEl = document.createElement('div');
    eventEl.className = `event ${event.event_type}`;

    // Parse event data
    const eventData = event.data || {};
    const endpoint = eventData.endpoint || '';
    const model = eventData.model || event.model || '';
    const messages = eventData.messages || [];
    const stream = eventData.stream || false;
    const content = eventData.content || '';
    const usage = eventData.usage || null;
    const policyName = eventData.policy_name || '';
    const eventName = eventData.event_name || '';
    const description = eventData.description || '';
    const chunkIndex = eventData.chunk_index;
    const totalChunks = eventData.total_chunks;
    const finishReason = eventData.finish_reason || '';
    const contentPreview = eventData.content_preview || '';

    // Build summary (always visible)
    let summaryHtml = '';

    // Add call_id to all events
    summaryHtml += `<div><strong>Call ID:</strong> <span class="call-id">${event.call_id}</span></div>`;

    // Event-specific summary
    if (event.event_type === 'gateway.request_received') {
        summaryHtml += `
            <div><strong>Endpoint:</strong> <code>${endpoint}</code></div>
            <div><strong>Model:</strong> <code>${model}</code> | <strong>Stream:</strong> ${stream ? 'Yes' : 'No'} | <strong>Messages:</strong> ${messages.length}</div>
        `;
    } else if (event.event_type === 'gateway.request_sent') {
        summaryHtml += `<div><strong>Model:</strong> <code>${model}</code> | <strong>Stream:</strong> ${stream ? 'Yes' : 'No'}</div>`;
    } else if (event.event_type === 'gateway.response_received' || event.event_type === 'gateway.response_sent') {
        const contentSnippet = content ? content.substring(0, 80) : '';
        summaryHtml += `<div><strong>Model:</strong> <code>${model}</code></div>`;
        if (contentSnippet) {
            summaryHtml += `<div><strong>Content:</strong> ${contentSnippet}${content.length > 80 ? '...' : ''}</div>`;
        }
        if (usage) {
            summaryHtml += `<div><strong>Tokens:</strong> ${usage.total_tokens || 'N/A'}</div>`;
        }
    } else if (event.event_type === 'streaming.chunk_received' || event.event_type === 'streaming.chunk_sent') {
        summaryHtml += `<div><strong>Chunk:</strong> #${chunkIndex}</div>`;
        if (contentPreview) {
            summaryHtml += `<div><strong>Preview:</strong> ${contentPreview}</div>`;
        }
        if (finishReason) {
            summaryHtml += `<div><strong>Finish:</strong> ${finishReason}</div>`;
        }
    } else if (event.event_type === 'streaming.original_complete' || event.event_type === 'streaming.transformed_complete') {
        const contentSnippet = content ? content.substring(0, 80) : '';
        summaryHtml += `<div><strong>Total Chunks:</strong> ${totalChunks || 'N/A'} | <strong>Finish:</strong> ${finishReason || 'N/A'}</div>`;
        if (contentSnippet) {
            summaryHtml += `<div><strong>Content:</strong> ${contentSnippet}${content.length > 80 ? '...' : ''}</div>`;
        }
    } else if (event.event_type === 'policy_event' || event.event_type.startsWith('policy.')) {
        if (policyName) summaryHtml += `<div><strong>Policy:</strong> <code>${policyName}</code></div>`;
        if (eventName) summaryHtml += `<div><strong>Event:</strong> ${eventName}</div>`;
        if (description) summaryHtml += `<div><strong>Description:</strong> ${description}</div>`;
    } else {
        // Generic summary for unknown events
        summaryHtml += `<div><em>Click to view full event data</em></div>`;
    }

    // Build full details (collapsible)
    const detailsHtml = `<pre>${JSON.stringify(event, null, 2)}</pre>`;

    // Build complete HTML
    eventEl.innerHTML = `
        <div class="event-header collapsed">
            <span class="event-type">
                <span class="expand-icon"></span>
                ${event.event_type}
            </span>
            <span class="event-time">${formatTime(event.timestamp)}</span>
        </div>
        <div class="event-summary">${summaryHtml}</div>
        <div class="event-details collapsed">${detailsHtml}</div>
    `;

    // Add click handler for collapse/expand
    const header = eventEl.querySelector('.event-header');
    const details = eventEl.querySelector('.event-details');

    header.addEventListener('click', () => {
        header.classList.toggle('collapsed');
        details.classList.toggle('collapsed');
    });

    return eventEl;
}

function addEvent(event) {
    // Store event for filtering
    allEvents.unshift(event);
    if (allEvents.length > 100) {
        allEvents.pop();
    }

    // Remove empty state if present
    const emptyState = eventFeedEl.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    const eventEl = renderEvent(event);

    // Apply filters immediately to new event
    if (!matchesFilters(event)) {
        eventEl.classList.add('filtered');
    }

    eventFeedEl.insertBefore(eventEl, eventFeedEl.firstChild);

    eventCount++;
    updateStatus(true);

    // Limit to 100 events
    while (eventFeedEl.children.length > 100) {
        eventFeedEl.removeChild(eventFeedEl.lastChild);
        allEvents.pop();
    }
}

function connect() {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource('/v2/activity/stream');

    eventSource.onopen = () => {
        console.log('Connected to activity stream');
        updateStatus(true);
    };

    eventSource.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            addEvent(event);
        } catch (err) {
            console.error('Failed to parse event:', err);
        }
    };

    eventSource.addEventListener('heartbeat', (e) => {
        console.log('Heartbeat received');
    });

    eventSource.addEventListener('error', (e) => {
        console.error('EventSource error');
        updateStatus(false);
    });

    eventSource.onerror = () => {
        console.error('Connection error, will retry...');
        updateStatus(false);
        eventSource.close();

        // Retry after 3 seconds
        setTimeout(connect, 3000);
    };
}

clearBtn.addEventListener('click', () => {
    eventFeedEl.innerHTML = '<div class="empty-state">Events cleared. Waiting for new events...</div>';
    eventCount = 0;
    allEvents = [];
    updateStatus(eventSource && eventSource.readyState === EventSource.OPEN);
});

reconnectBtn.addEventListener('click', () => {
    connect();
});

// Wire up filter inputs
filterCallId.addEventListener('input', applyFilters);
filterModel.addEventListener('input', applyFilters);

// Multiselect event type filter
eventTypeButton.addEventListener('click', (e) => {
    e.stopPropagation();
    eventTypeDropdown.classList.toggle('open');
});

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!eventTypeDropdown.contains(e.target) && !eventTypeButton.contains(e.target)) {
        eventTypeDropdown.classList.remove('open');
    }
});

// Handle category header clicks (toggle all/none in category)
eventTypeDropdown.querySelectorAll('.category-header').forEach(header => {
    header.addEventListener('click', (e) => {
        e.stopPropagation();
        const category = header.dataset.category;
        const categoryDiv = header.closest('.multiselect-category');
        const checkboxes = categoryDiv.querySelectorAll('input[type="checkbox"]');

        // Check if all are currently checked
        const allChecked = Array.from(checkboxes).every(cb => cb.checked);

        // Toggle: if all checked, uncheck all; otherwise check all
        checkboxes.forEach(cb => {
            cb.checked = !allChecked;
            if (cb.checked) {
                selectedEventTypes.add(cb.value);
            } else {
                selectedEventTypes.delete(cb.value);
            }
        });

        updateEventTypeLabel();
        applyFilters();
    });
});

// Handle individual checkbox changes
eventTypeDropdown.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
    checkbox.addEventListener('change', (e) => {
        e.stopPropagation();
        if (checkbox.checked) {
            selectedEventTypes.add(checkbox.value);
        } else {
            selectedEventTypes.delete(checkbox.value);
        }
        updateEventTypeLabel();
        applyFilters();
    });
});

// Prevent dropdown from closing when clicking inside it
eventTypeDropdown.addEventListener('click', (e) => {
    e.stopPropagation();
});

// Auto-connect on load
connect();
