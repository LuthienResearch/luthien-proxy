// Simple conversation monitor - rebuilt from scratch
const state = {
  activeCallId: null,
  calls: [],
};

// Fetch and display recent calls
async function loadRecentCalls() {
  try {
    const response = await fetch('/api/hooks/recent_call_ids?limit=50');
    const calls = await response.json();
    renderCallList(calls);
  } catch (err) {
    console.error('Failed to load recent calls:', err);
    document.getElementById('call-list').textContent = 'Failed to load calls';
  }
}

// Render the list of calls in the sidebar
function renderCallList(calls) {
  const list = document.getElementById('call-list');
  list.innerHTML = '';

  if (!calls || calls.length === 0) {
    list.textContent = 'No recent calls';
    return;
  }

  calls.forEach(call => {
    const item = document.createElement('div');
    item.className = 'call-item';
    if (call.call_id === state.activeCallId) {
      item.classList.add('active');
    }

    const callId = document.createElement('div');
    callId.className = 'call-id';
    callId.textContent = call.call_id;
    item.appendChild(callId);

    const meta = document.createElement('div');
    meta.className = 'call-meta';
    meta.textContent = `${new Date(call.latest).toLocaleString()} • ${call.count} events`;
    item.appendChild(meta);

    item.addEventListener('click', () => loadConversation(call.call_id));
    list.appendChild(item);
  });
}

// Load and display a specific conversation
async function loadConversation(callId) {
  console.log('Loading conversation for call:', callId);
  state.activeCallId = callId;

  // Update UI to show loading
  document.getElementById('active-call').textContent = `Call: ${callId}`;
  document.getElementById('status').textContent = 'Loading...';
  const timeline = document.getElementById('timeline');
  timeline.textContent = 'Loading conversation...';

  try {
    const response = await fetch(`/api/hooks/conversation?call_id=${encodeURIComponent(callId)}`);
    const data = await response.json();
    console.log('Loaded conversation data:', data);
    renderConversation(data);
    document.getElementById('status').textContent = 'Loaded';
  } catch (err) {
    console.error('Failed to load conversation:', err);
    timeline.textContent = 'Failed to load conversation: ' + err.message;
    document.getElementById('status').textContent = 'Error';
  }
}

// Render the conversation data
function renderConversation(data) {
  const timeline = document.getElementById('timeline');
  timeline.innerHTML = '';

  if (!data.calls || data.calls.length === 0) {
    timeline.textContent = 'No conversation data available';
    return;
  }

  data.calls.forEach(call => {
    const card = document.createElement('div');
    card.className = 'call-card';

    // Header
    const header = document.createElement('div');
    header.className = 'call-header';

    const title = document.createElement('div');
    title.className = 'call-title';
    title.textContent = call.call_id;
    header.appendChild(title);

    const status = document.createElement('span');
    status.className = `status-badge ${call.status}`;
    status.textContent = call.status;
    header.appendChild(status);

    card.appendChild(header);

    // Request section
    const requestSection = document.createElement('div');
    requestSection.className = 'section';

    const requestTitle = document.createElement('h3');
    requestTitle.textContent = 'Request';
    requestSection.appendChild(requestTitle);

    if (call.request_final_messages && call.request_final_messages.length > 0) {
      call.request_final_messages.forEach(msg => {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${msg.role}`;

        const roleSpan = document.createElement('strong');
        roleSpan.textContent = msg.role + ': ';
        msgDiv.appendChild(roleSpan);

        const contentSpan = document.createElement('span');
        contentSpan.textContent = msg.content;
        msgDiv.appendChild(contentSpan);

        requestSection.appendChild(msgDiv);
      });
    } else {
      requestSection.appendChild(document.createTextNode('No request messages'));
    }

    card.appendChild(requestSection);

    // Response section
    const responseSection = document.createElement('div');
    responseSection.className = 'section';

    const responseTitle = document.createElement('h3');
    responseTitle.textContent = 'Response';
    responseSection.appendChild(responseTitle);

    const responseDiv = document.createElement('div');
    responseDiv.className = 'message assistant';
    responseDiv.textContent = call.final_response || 'No response';
    responseSection.appendChild(responseDiv);

    card.appendChild(responseSection);
    timeline.appendChild(card);
  });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
  console.log('Conversation monitor initialized');

  // Load recent calls on startup
  loadRecentCalls();

  // Setup manual load button
  const loadBtn = document.getElementById('call-load');
  if (loadBtn) {
    loadBtn.addEventListener('click', () => {
      const input = document.getElementById('call-input');
      const callId = input ? input.value.trim() : '';
      if (callId) {
        loadConversation(callId);
      }
    });
  }

  // Setup refresh button
  const refreshBtn = document.getElementById('call-refresh');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', loadRecentCalls);
  }

  // Setup reload button
  const reloadBtn = document.getElementById('call-reload');
  if (reloadBtn) {
    reloadBtn.addEventListener('click', () => {
      if (state.activeCallId) {
        loadConversation(state.activeCallId);
      }
    });
  }
});
