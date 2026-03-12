/* Shared navigation component for all Luthien UI pages.
 *
 * Usage: add <nav x-data="luthienNav"></nav> at the top of <body>,
 * include nav.css, and load Alpine.js. The nav renders itself via
 * Alpine's x-init and template directives.
 */
document.addEventListener('alpine:init', () => {
    Alpine.data('luthienNav', () => ({
        currentPath: window.location.pathname,
        links: [
            { href: '/activity/monitor', label: 'Activity' },
            { href: '/history', label: 'History' },
            { href: '/diffs', label: 'Diffs' },
            { href: '/policy-config', label: 'Policies' },
            { href: '/credentials', label: 'Credentials' },
            { href: '/client-setup', label: 'Client Setup' },
        ],
        async init() {
            const navEl = this.$el;
            const updateBadge = async () => {
                // Show a billing mode badge so users know whether requests are billed
                // to a server-side Anthropic API key or forwarded via Claude Pro/Max OAuth.
                try {
                    const data = await fetch('/health').then(r => r.json());
                    let badgeTitle = null;
                    if (data.auth_mode === 'proxy_key' || data.last_credential_type === 'proxy_key_fallback') {
                        badgeTitle =
                            'Requests are billed to the server ANTHROPIC_API_KEY. ' +
                            'To use your Claude Pro/Max subscription instead, ' +
                            'remove ANTHROPIC_API_KEY from .env and restart the gateway.';
                    } else if (data.last_credential_type === 'client_api_key') {
                        badgeTitle =
                            'Requests are billed to your Anthropic API key, not your Claude Pro/Max subscription. ' +
                            'To use Claude Max instead, run: claude auth login';
                    }
                    const existing = navEl.querySelector('.nav-billing-badge');
                    if (badgeTitle && !existing) {
                        const badge = document.createElement('span');
                        badge.className = 'nav-billing-badge';
                        badge.textContent = '⚠ API billing';
                        badge.title = badgeTitle;
                        const spacer = navEl.querySelector('.nav-spacer');
                        if (spacer) spacer.before(badge);
                    } else if (badgeTitle && existing) {
                        existing.title = badgeTitle;
                    } else if (!badgeTitle && existing) {
                        existing.remove();
                    }
                } catch (_) {
                    // Health check failure is not fatal — nav still works without the badge.
                }
            };
            await updateBadge();
            const intervalId = setInterval(updateBadge, 30000);
            this.$cleanup(() => clearInterval(intervalId));
        },
        isActive(href) {
            if (href === '/') return this.currentPath === '/';
            return this.currentPath.startsWith(href);
        },
        isAuthenticated() {
            // Check if user has a session cookie (luthien_session)
            return document.cookie.split(';').some(cookie =>
                cookie.trim().startsWith('luthien_session=')
            );
        }
    }));
});
