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
            { href: '/history', label: 'History' },
            { href: '/policy-config', label: 'Policies' },
            { href: '/diffs', label: 'Diffs' },
            { href: '/credentials', label: 'Credentials' },
            { href: '/inference-providers', label: 'Providers' },
            { href: '/config', label: 'Config' },
        ],
        async init() {
            const navEl = this.$el;
            // Sticky-yellow: once any request in this page session used an API key,
            // the badge stays yellow until ALL traffic routes through OAuth.
            // This avoids confusing badge flicker in "both" auth mode.
            // One-way latch: once true, stays true for the page session.
            // Users must reload to see the green badge after switching from API key to OAuth.
            let sawApiKey = false;
            let activeTooltip = null;
            let activeBadge = null;

            const positionTooltip = () => {
                if (!activeTooltip || !activeBadge) return;
                if (!activeTooltip.classList.contains('visible')) return;
                const infoBtn = activeBadge.querySelector('.nav-billing-info');
                const anchor = infoBtn || activeBadge;
                const rect = anchor.getBoundingClientRect();
                // Position to the right of the ⓘ icon, with left-pointing caret
                activeTooltip.style.top = (rect.top - 4) + 'px';
                activeTooltip.style.left = (rect.right + 10) + 'px';
                activeTooltip.style.right = 'auto';
            };

            const closeTooltip = () => {
                if (activeTooltip) {
                    activeTooltip.classList.remove('visible');
                }
            };

            // Page-lifetime flag: stop polling once we know we're not
            // authenticated (e.g. anonymous visitors loading the public
            // landing page). Avoids 30s 403 noise in access logs.
            let billingStatusUnauthorized = false;
            const updateBadge = async () => {
                if (billingStatusUnauthorized) return;
                let data;
                try {
                    // Authenticated endpoint — uses the admin session cookie
                    // set by /auth/login. Returns 403 when unauthenticated;
                    // we then leave the badge unset.
                    const response = await fetch('/api/admin/billing-status', {
                        credentials: 'same-origin',
                    });
                    if (response.status === 403) {
                        billingStatusUnauthorized = true;
                        return;
                    }
                    if (!response.ok) return;
                    data = await response.json();
                    let badgeType = null; // 'warning' or 'ok'
                    let badgeLabel = null;
                    let tooltipText = null;

                    const isApiKeyCredential =
                        data.auth_mode === 'client_key' ||
                        data.last_credential_type === 'client_key_match' ||
                        data.last_credential_type === 'user_api_key';

                    if (isApiKeyCredential) {
                        sawApiKey = true;
                    }

                    if (data.auth_mode === 'client_key') {
                        badgeType = 'warning';
                        badgeLabel = '⚠ API key billing';
                        tooltipText =
                            'Traffic is using an API key. You are being billed per token.';
                    } else if (sawApiKey) {
                        badgeType = 'warning';
                        badgeLabel = '⚠ API key billing';
                        if (data.last_credential_type === 'user_api_key') {
                            tooltipText =
                                'Traffic is using your Anthropic API key, not your Claude subscription. ' +
                                'To use Claude Max instead, run: claude auth login';
                        } else {
                            tooltipText =
                                'Some requests are billed to an API key. ' +
                                'You are being billed per token for at least some traffic.';
                        }
                    } else if (
                        data.last_credential_type === 'oauth' ||
                        data.last_credential_type === 'oauth_via_api_key'
                    ) {
                        badgeType = 'ok';
                        badgeLabel = '✔ Claude plan active';
                        tooltipText =
                            'Usage applies to your Claude subscription. No per-token charges.';
                    }

                    const existing = navEl.querySelector('.nav-billing-badge');
                    if (badgeType) {
                        if (!existing) {
                            const { badge, tip } = createBadgeElement(badgeType, badgeLabel, tooltipText);
                            const navRight = navEl.querySelector('.nav-right');
                            if (navRight) navRight.prepend(badge);
                            document.body.appendChild(tip);
                            activeBadge = badge;
                            activeTooltip = tip;
                        } else {
                            const oldType = existing.className.includes('--warning') ? 'warning' : 'ok';
                            if (oldType !== badgeType) closeTooltip();
                            existing.className = 'nav-billing-badge nav-billing-badge--' + badgeType;
                            const label = existing.querySelector('.nav-billing-label');
                            if (label) label.textContent = badgeLabel;
                            if (activeTooltip) activeTooltip.textContent = tooltipText;
                        }
                    } else {
                        if (existing) existing.remove();
                        if (activeTooltip) {
                            activeTooltip.remove();
                            activeTooltip = null;
                            activeBadge = null;
                        }
                    }
                } catch (_) {
                    // Billing-status fetch failure is not fatal — nav still
                    // works without the badge. The 403 path (unauthenticated)
                    // is handled by the !response.ok early-return above.
                }
                return data;
            };

            function createBadgeElement(type, label, tooltip) {
                const badge = document.createElement('span');
                badge.className = 'nav-billing-badge nav-billing-badge--' + type;
                badge.setAttribute('role', 'status');

                const labelSpan = document.createElement('span');
                labelSpan.className = 'nav-billing-label';
                labelSpan.textContent = label;
                badge.appendChild(labelSpan);

                const tipId = 'nav-billing-tip';

                const infoBtn = document.createElement('span');
                infoBtn.className = 'nav-billing-info';
                // Solid filled ⓘ icon via inline SVG (no user input — static markup)
                const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                svg.setAttribute('width', '14');
                svg.setAttribute('height', '14');
                svg.setAttribute('viewBox', '0 0 16 16');
                svg.setAttribute('fill', 'currentColor');
                const bg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                bg.setAttribute('cx', '8'); bg.setAttribute('cy', '8'); bg.setAttribute('r', '8');
                bg.setAttribute('opacity', '0.3');
                const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                dot.setAttribute('cx', '8'); dot.setAttribute('cy', '4.5'); dot.setAttribute('r', '1.2');
                const bar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                bar.setAttribute('x', '7'); bar.setAttribute('y', '6.8');
                bar.setAttribute('width', '2'); bar.setAttribute('height', '5');
                bar.setAttribute('rx', '0.5');
                svg.append(bg, dot, bar);
                infoBtn.appendChild(svg);
                infoBtn.setAttribute('role', 'button');
                infoBtn.setAttribute('aria-label', 'Billing info');
                // aria-describedby points to the tooltip which is appended to
                // document.body (not inside the nav) so it can use fixed positioning
                // without being clipped by the nav's overflow.
                infoBtn.setAttribute('aria-describedby', tipId);
                infoBtn.setAttribute('tabindex', '0');
                badge.appendChild(infoBtn);

                const tip = document.createElement('span');
                tip.className = 'nav-billing-tooltip';
                tip.id = tipId;
                tip.setAttribute('role', 'tooltip');
                tip.textContent = tooltip;

                infoBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const isVisible = tip.classList.contains('visible');
                    closeTooltip();
                    if (!isVisible) {
                        tip.classList.add('visible');
                        positionTooltip();
                    }
                });

                infoBtn.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        infoBtn.click();
                    }
                });

                return { badge, tip };
            }

            document.addEventListener('click', closeTooltip);
            window.addEventListener('resize', positionTooltip);

            // Version comes from the public /health endpoint; billing signals
            // come from the admin-gated /api/admin/billing-status. Two fetches
            // keep each endpoint single-purpose; we issue them in parallel so
            // the page renders in a single round-trip.
            const [, healthResponse] = await Promise.all([
                updateBadge(),
                fetch('/health').catch(() => null),
            ]);
            const intervalId = setInterval(updateBadge, 30000);

            let footer = null;
            try {
                if (healthResponse && healthResponse.ok) {
                    const healthData = await healthResponse.json();
                    if (healthData && healthData.version) {
                        footer = document.createElement('footer');
                        footer.className = 'luthien-footer';
                        footer.textContent = 'luthien-proxy @ ' + healthData.version;
                        document.body.appendChild(footer);
                    }
                }
            } catch (e) {
                // Footer is non-critical; leave it absent on fetch/parse error.
            }

            this.$cleanup(() => {
                clearInterval(intervalId);
                if (activeTooltip) activeTooltip.remove();
                if (footer) footer.remove();
                document.removeEventListener('click', closeTooltip);
                window.removeEventListener('resize', positionTooltip);
            });
        },
        isActive(href) {
            if (href === '/') return this.currentPath === '/';
            return this.currentPath.startsWith(href);
        },
        isAuthenticated() {
            return document.cookie.split(';').some(cookie =>
                cookie.trim().startsWith('luthien_session=')
            );
        }
    }));
});
