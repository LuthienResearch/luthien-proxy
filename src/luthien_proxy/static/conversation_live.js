
let _turnsLoading = false;
async function loadMoreTurns() {
    if (_turnsLoading || !window.__turnsCursor) return;
    _turnsLoading = true;
    const cursor = window.__turnsCursor;
    try {
        window.__turnsCursor = null;
        const sessionId = window.__sessionId;
        const url = `/ui/fragments/sessions/${sessionId}/turns?limit=10&cursor=${encodeURIComponent(cursor)}`;
        const resp = await fetch(url, { headers: { 'Accept': 'text/html' } });

        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }

        const html = await resp.text();
        const container = document.getElementById('conversation-container');
        const loadMoreEl = document.getElementById('turns-load-more');
        
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = html;
        
        const sentinel = tempDiv.querySelector('.load-more-sentinel[data-cursor]');
        if (sentinel) {
            window.__turnsCursor = sentinel.dataset.cursor;
            sentinel.remove();
        }
        
        if (loadMoreEl) {
            loadMoreEl.insertAdjacentHTML('beforebegin', tempDiv.innerHTML);
        } else if (container) {
            container.insertAdjacentHTML('beforeend', tempDiv.innerHTML);
        }

        if (window.Alpine) {
            window.Alpine.initTree(container);
        }
    } catch (e) {
        console.error('Failed to load more turns:', e);
        if (cursor) window.__turnsCursor = cursor;
    } finally {
        _turnsLoading = false;
    }
}