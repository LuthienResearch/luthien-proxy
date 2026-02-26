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
            { href: '/request-logs/viewer', label: 'Logs' },
        ],
        isActive(href) {
            if (href === '/') return this.currentPath === '/';
            return this.currentPath.startsWith(href);
        }
    }));
});
