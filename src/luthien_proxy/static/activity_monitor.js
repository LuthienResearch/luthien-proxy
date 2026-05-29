// Activity Monitor - Clean and reliable event streaming

let eventSource = null;
let eventCount = 0;
let allEvents = [];

// DOM elements
const statusEl = document.getElementById('status');
const statusTextEl = document.getElementById('status-text');
const eventsEl = document.getElementById('events');
const idFilterEl = document.getElementById('id-filter');
const typeFilterBtnEl = document.getElementById('type-filter-btn');
const typeFilterDropdownEl = document.getElementById('type-filter-dropdown');
const typeFilterLabelEl = document.getElementById('type-filter-label');
const clearBtnEl = document.getElementById('clear-btn');
const reconnectBtnEl = document.getElementById('reconnect-btn');

// Dynamic event type tracking
// Structure: { "transaction": ["request_recorded", "streaming_response_recorded"], ... }
const discoveredEventTypes = new Map();
const selectedEventTypes = new Set(); // Set of full event type strings (e.g., "transaction.request_recorded")
let selectAllChecked = true;

// Update connection status
function updateStatus(connected) {
    if (connected) {
        statusEl.className = 'status connected';
        statusTextEl.textContent = `Connected (${eventCount} events)`;
    } else {
        statusEl.className = 'status disconnected';
        statusTextEl.textContent = 'Disconnected';
    }
}

// Format timestamp
function formatTime(timestamp) {
    const date = new Date(timestamp);
    const time = date.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
    const ms = date.getMilliseconds().toString().padStart(3, '0');
    return `${time}.${ms}`;
}

// Register discovered event type
function registerEventType(eventType) {
    if (!eventType || typeof eventType !== 'string') return;

    const parts = eventType.split('.');
    if (parts.length < 2) return; // Skip malformed event types

    const category = parts[0];
    const subtype = parts.slice(1).join('.');

    if (!discoveredEventTypes.has(category)) {
        discoveredEventTypes.set(category, new Set());
    }

    const subtypes = discoveredEventTypes.get(category);
    const wasNew = !subtypes.has(subtype);
    subtypes.add(subtype);

    // Auto-select new event types if "Select All" is checked
    if (selectAllChecked) {
        selectedEventTypes.add(eventType);
    }

    // Rebuild filter UI if this was a new type
    if (wasNew) {
        rebuildFilterDropdown();
    }
}

// Check if event matches current filters
function matchesFilters(event) {
    // ID filter (checks both call_id and transaction_id)
    const idFilter = idFilterEl.value.trim().toLowerCase();
    if (idFilter) {
        const callId = (event.call_id || '').toLowerCase();
        const transactionId = (event.transaction_id || '').toLowerCase();
        if (!callId.includes(idFilter) && !transactionId.includes(idFilter)) {
            return false;
        }
    }

    // Event type filter
    if (selectAllChecked) {
        return true; // "Select All" means show everything
    }

    const eventType = event.event_type || '';
    return selectedEventTypes.has(eventType);
}

// Apply filters to all events
function applyFilters() {
    const eventElements = eventsEl.querySelectorAll('.event');

    eventElements.forEach((el, idx) => {
        const event = allEvents[idx];
        if (event && matchesFilters(event)) {
            el.classList.remove('hidden');
        } else {
            el.classList.add('hidden');
        }
    });
}

// Rebuild the filter dropdown dynamically.
//
// DOM construction: `category` / `subtype` are derived from event_type (split
// on '.'), which comes from the activity stream and is request-derived. They
// were previously interpolated into innerHTML including id="" / value=""
// attributes — same stored-XSS class. Build nodes; markup/quotes are inert.
function rebuildFilterDropdown() {
    typeFilterDropdownEl.replaceChildren();

    // Add "Select All" option
    const selectAllDiv = document.createElement('div');
    selectAllDiv.className = 'filter-option';
    const selectAllInput = document.createElement('input');
    selectAllInput.type = 'checkbox';
    selectAllInput.id = 'select-all-types';
    selectAllInput.checked = selectAllChecked;
    const selectAllLabel = document.createElement('label');
    selectAllLabel.htmlFor = 'select-all-types';
    const selectAllStrong = document.createElement('strong');
    selectAllStrong.textContent = 'Select All';
    selectAllLabel.appendChild(selectAllStrong);
    selectAllDiv.append(selectAllInput, selectAllLabel);
    typeFilterDropdownEl.appendChild(selectAllDiv);

    // Add categories and subtypes
    const sortedCategories = Array.from(discoveredEventTypes.keys()).sort();

    sortedCategories.forEach(category => {
        const subtypes = Array.from(discoveredEventTypes.get(category)).sort();

        // Category header (clickable to toggle all in category)
        const categoryDiv = document.createElement('div');
        categoryDiv.className = 'filter-category';
        const headerOption = document.createElement('div');
        headerOption.className = 'filter-option category-header';
        headerOption.dataset.category = category;
        const headerLabel = document.createElement('label');
        const headerStrong = document.createElement('strong');
        headerStrong.textContent = category;
        headerLabel.append(headerStrong, document.createTextNode(` (${subtypes.length})`));
        headerOption.appendChild(headerLabel);
        categoryDiv.appendChild(headerOption);
        typeFilterDropdownEl.appendChild(categoryDiv);

        // Subtypes
        subtypes.forEach(subtype => {
            const fullType = `${category}.${subtype}`;
            const isSelected = selectedEventTypes.has(fullType);
            const checkboxId = `type-${category}-${subtype}`;

            const subtypeDiv = document.createElement('div');
            subtypeDiv.className = 'filter-option filter-subtype';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.id = checkboxId;
            input.value = fullType;
            input.checked = isSelected;
            const label = document.createElement('label');
            label.htmlFor = checkboxId;
            label.textContent = subtype;
            subtypeDiv.append(input, label);
            typeFilterDropdownEl.appendChild(subtypeDiv);
        });
    });

    // Attach event listeners
    attachFilterListeners();
    updateTypeFilterLabel();
}

// Update event type filter label
function updateTypeFilterLabel() {
    if (selectAllChecked) {
        typeFilterLabelEl.textContent = 'All Events';
        return;
    }

    const selectedCount = selectedEventTypes.size;
    if (selectedCount === 0) {
        typeFilterLabelEl.textContent = 'None';
    } else {
        typeFilterLabelEl.textContent = `${selectedCount} selected`;
    }
}

// Create event DOM element.
//
// DOM construction (not innerHTML): every value here comes from the activity
// stream, which is derived from live request traffic — event_type,
// transaction_id, and the full event payload are attacker-influenceable. The
// previous innerHTML build interpolated `event_type` as HTML text and the whole
// event via `JSON.stringify(event)` into a <pre> with NO escaping; JSON.stringify
// does not escape <, >, so a payload containing "</pre><script>..." injected
// script. Building nodes with textContent makes every field inert.
function createEventElement(event) {
    const el = document.createElement('div');
    el.className = 'event';
    el.dataset.type = event.event_type || 'unknown';

    // Extract relevant IDs
    const callId = event.call_id || 'N/A';
    const transactionId = event.transaction_id || callId;
    const timestamp = event.timestamp || new Date().toISOString();

    const headerDiv = document.createElement('div');
    headerDiv.className = 'event-header';

    const mainDiv = document.createElement('div');
    mainDiv.className = 'event-main';

    const typeDiv = document.createElement('div');
    typeDiv.className = 'event-type';
    typeDiv.textContent = event.event_type || 'unknown';

    const metaDiv = document.createElement('div');
    metaDiv.className = 'event-meta';
    const idSpan = document.createElement('span');
    idSpan.className = 'event-id';
    idSpan.textContent = `ID: ${String(transactionId).substring(0, 12)}...`;
    const timeSpan = document.createElement('span');
    timeSpan.className = 'event-time';
    timeSpan.textContent = formatTime(timestamp);
    metaDiv.append(idSpan, timeSpan);

    mainDiv.append(typeDiv, metaDiv);

    const expandIcon = document.createElement('div');
    expandIcon.className = 'expand-icon';
    expandIcon.textContent = '▼';

    headerDiv.append(mainDiv, expandIcon);

    const payloadDiv = document.createElement('div');
    payloadDiv.className = 'event-payload';
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(event, null, 2);
    payloadDiv.appendChild(pre);

    el.append(headerDiv, payloadDiv);

    // Add click handler for expand/collapse
    headerDiv.addEventListener('click', () => {
        el.classList.toggle('expanded');
    });

    return el;
}

// Attach event listeners to filter checkboxes
function attachFilterListeners() {
    // "Select All" checkbox
    const selectAllCheckbox = document.getElementById('select-all-types');
    if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener('change', () => {
            selectAllChecked = selectAllCheckbox.checked;

            if (selectAllChecked) {
                // Select all discovered event types
                selectedEventTypes.clear();
                discoveredEventTypes.forEach((subtypes, category) => {
                    subtypes.forEach(subtype => {
                        selectedEventTypes.add(`${category}.${subtype}`);
                    });
                });
            }

            rebuildFilterDropdown();
            applyFilters();
        });
    }

    // Category headers (toggle all in category)
    document.querySelectorAll('.category-header').forEach(header => {
        header.addEventListener('click', () => {
            const category = header.dataset.category;
            const subtypes = discoveredEventTypes.get(category);

            // Check if all subtypes in this category are selected
            const allSelected = Array.from(subtypes).every(subtype =>
                selectedEventTypes.has(`${category}.${subtype}`)
            );

            // Toggle: if all selected, deselect all; otherwise select all
            subtypes.forEach(subtype => {
                const fullType = `${category}.${subtype}`;
                if (allSelected) {
                    selectedEventTypes.delete(fullType);
                } else {
                    selectedEventTypes.add(fullType);
                }
            });

            selectAllChecked = false;
            rebuildFilterDropdown();
            applyFilters();
        });
    });

    // Individual subtype checkboxes
    document.querySelectorAll('.filter-subtype input[type="checkbox"]').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            const fullType = checkbox.value;

            if (checkbox.checked) {
                selectedEventTypes.add(fullType);
            } else {
                selectedEventTypes.delete(fullType);
                selectAllChecked = false;
            }

            updateTypeFilterLabel();
            applyFilters();
        });
    });
}

// Add new event
function addEvent(event) {
    // Register event type for dynamic filter
    if (event.event_type) {
        registerEventType(event.event_type);
    }

    // Remove empty state if present
    const emptyState = eventsEl.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    // Store event
    allEvents.unshift(event);
    if (allEvents.length > 200) {
        allEvents.pop();
    }

    // Create and insert element
    const el = createEventElement(event);

    // Apply filters to new event
    if (!matchesFilters(event)) {
        el.classList.add('hidden');
    }

    eventsEl.insertBefore(el, eventsEl.firstChild);

    // Limit DOM to 200 events
    while (eventsEl.children.length > 200) {
        eventsEl.removeChild(eventsEl.lastChild);
    }

    eventCount++;
    updateStatus(true);
}

// Connect to event stream
function connect() {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource('/api/activity/stream');

    eventSource.onopen = () => {
        console.log('Connected to activity stream');
        updateStatus(true);
    };

    eventSource.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            addEvent(event);
        } catch (err) {
            console.error('Failed to parse event:', err, e.data);
        }
    };

    eventSource.addEventListener('heartbeat', () => {
        // Just keep connection alive
        console.log('Heartbeat');
    });

    eventSource.onerror = () => {
        console.error('Connection error, reconnecting in 3s...');
        updateStatus(false);
        eventSource.close();
        setTimeout(connect, 3000);
    };
}

// Clear events
clearBtnEl.addEventListener('click', () => {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = 'Events cleared. Waiting for new events...';
    eventsEl.replaceChildren(empty);
    allEvents = [];
    eventCount = 0;
    updateStatus(eventSource && eventSource.readyState === EventSource.OPEN);
});

// Reconnect
reconnectBtnEl.addEventListener('click', () => {
    connect();
});

// ID filter input
idFilterEl.addEventListener('input', applyFilters);

// Event type filter dropdown toggle
typeFilterBtnEl.addEventListener('click', (e) => {
    e.stopPropagation();
    typeFilterDropdownEl.classList.toggle('open');
});

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!typeFilterDropdownEl.contains(e.target) && !typeFilterBtnEl.contains(e.target)) {
        typeFilterDropdownEl.classList.remove('open');
    }
});

// Initialize and auto-connect
rebuildFilterDropdown(); // Initial empty dropdown
connect();
