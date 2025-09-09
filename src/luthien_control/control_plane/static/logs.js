function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text || ''; return div.innerHTML; }

async function fetchLogsOnce() {
  const limit = document.getElementById('limit').value;
  const tbody = document.getElementById('logs');
  const err = document.getElementById('error');
  try {
    const res = await fetch(`/api/logs?limit=${encodeURIComponent(limit)}`);
    if (!res.ok) throw new Error('Failed to fetch logs');
    const logs = await res.json();
    if (!Array.isArray(logs) || logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#666;">No logs found</td></tr>';
      err.style.display = 'none';
      return;
    }
    tbody.innerHTML = logs.map(log => `
      <tr>
        <td>${new Date(log.created_at).toLocaleString()}</td>
        <td><span class="stage stage-${log.stage}">${log.stage}</span></td>
        <td>${log.call_type || '-'}</td>
        <td class="truncate" title="${escapeHtml(log.request_summary)}">${escapeHtml(log.request_summary)}</td>
        <td class="truncate" title="${escapeHtml(log.response_summary || '')}">${escapeHtml(log.response_summary || '-')}</td>
        <td>${log.policy_action || '-'}</td>
        <td style="font-family: monospace; font-size: 11px;">${log.episode_id ? log.episode_id.slice(0, 8) : '-'}</td>
      </tr>
    `).join('');
    err.style.display = 'none';
  } catch (e) {
    err.textContent = 'Error: ' + (e && e.message ? e.message : e);
    err.style.display = 'block';
    tbody.innerHTML = '<tr><td colspan="7" class="loading">Failed to load logs</td></tr>';
  }
}

window.addEventListener('DOMContentLoaded', () => {
  let timer = null;
  document.getElementById('refresh').addEventListener('click', fetchLogsOnce);
  document.getElementById('limit').addEventListener('change', fetchLogsOnce);
  const auto = document.getElementById('autoRefresh');
  auto.addEventListener('change', () => {
    if (auto.checked) { timer = setInterval(fetchLogsOnce, 5000); }
    else if (timer) { clearInterval(timer); timer = null; }
  });
  fetchLogsOnce();
});
