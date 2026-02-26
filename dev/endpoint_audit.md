# Endpoint Audit: Non-Proxy Routes

**Date**: 2026-02-26
**Author**: Claude (endpoint-audit task)
**Scope**: All non-`/v1/` endpoints — API routes, UI pages, auth, debug, and utility endpoints

---

## 1. Current State Map

### Route Registration Order (main.py)

```
1. /static          — StaticFiles mount (1-hour cache)
2. gateway_router   — /v1/* (proxy endpoints, out of scope)
3. debug_router     — /debug/*
4. ui_router        — /, /activity/*, /debug/diff, /policy-config, /credentials, /conversation/live/*, /client-setup
5. admin_router     — /admin/*
6. session_router   — /auth/*
7. login_page_router — /login (convenience duplicate)
8. history_routes   — /history/*
9. @app.get /health — inline health check
```

**NOT registered**: `request_log/routes.py` defines a router at `/request-logs` with 2 endpoints, but it is never included in `main.py`.

---

### Module-by-Module Endpoint Inventory

#### A. UI Pages (HTML-serving)

| Path | Module | Auth | Nav Links Available | Notes |
|------|--------|------|---------------------|-------|
| `/` | ui/routes.py | Public | N/A (landing page, links to everything) | Light theme, acts as sitemap |
| `/activity/monitor` | ui/routes.py | Session/API key | Sign Out | Dark theme, no nav to other pages except sign out |
| `/debug/diff` | ui/routes.py | Session/API key | Sign Out | Dark theme, no nav to other pages |
| `/policy-config` | ui/routes.py | Session/API key | Activity Monitor, Diff Viewer, Sign Out | Dark theme, 3 header links |
| `/credentials` | ui/routes.py | Session/API key | Home, Activity Monitor, Policy Config, Sign Out | Dark theme, 4 header links |
| `/conversation/live/{id}` | ui/routes.py | Session/API key | ← Sessions (back link), Export, Sign Out | Dark theme |
| `/client-setup` | ui/routes.py | Public | ← Back to gateway, Policy Config, Activity Monitor, Gateway Home | Dark theme |
| `/history` | history/routes.py | Session/API key | Sign Out | Dark theme, no nav to other pages |
| `/history/session/{id}` | history/routes.py | Session/API key | ← Back to sessions, Live View, Export, Sign Out | Dark theme |
| `/login` | session.py | Public | ← Back to Gateway | Login form (dark theme) |
| `/auth/login` | session.py | Public | ← Back to Gateway | Duplicate of /login |

#### B. JSON API Endpoints

| Path | Method | Module | Auth | Purpose |
|------|--------|--------|------|---------|
| `/admin/policy/current` | GET | admin/routes.py | Admin token | Get active policy info |
| `/admin/policy/set` | POST | admin/routes.py | Admin token | Set/change active policy |
| `/admin/policy/list` | GET | admin/routes.py | Admin token | List available policy classes |
| `/admin/models` | GET | admin/routes.py | Admin token | List available LLM models |
| `/admin/test/chat` | POST | admin/routes.py | Admin token | Send test message through proxy |
| `/admin/auth/config` | GET | admin/routes.py | Admin token | Get auth configuration |
| `/admin/auth/config` | POST | admin/routes.py | Admin token | Update auth configuration |
| `/admin/auth/credentials` | GET | admin/routes.py | Admin token | List cached credentials |
| `/admin/auth/credentials/{hash}` | DELETE | admin/routes.py | Admin token | Invalidate single credential |
| `/admin/auth/credentials` | DELETE | admin/routes.py | Admin token | Invalidate all credentials |
| `/debug/calls` | GET | debug/routes.py | Admin token | List recent calls |
| `/debug/calls/{call_id}` | GET | debug/routes.py | Admin token | Get events for a call |
| `/debug/calls/{call_id}/diff` | GET | debug/routes.py | Admin token | Compute policy diff for a call |
| `/history/api/sessions` | GET | history/routes.py | Admin token | List recent sessions (paginated) |
| `/history/api/sessions/{id}` | GET | history/routes.py | Admin token | Get full session detail |
| `/history/api/sessions/{id}/export` | GET | history/routes.py | Admin token | Export session as markdown |
| `/activity/stream` | GET | ui/routes.py | Admin token | SSE activity stream |

#### C. Auth & Session Endpoints

| Path | Method | Module | Auth | Purpose |
|------|--------|--------|------|---------|
| `/auth/login` | POST | session.py | Public | Handle login form submission |
| `/auth/logout` | POST | session.py | Public | Clear session cookie |
| `/auth/logout` | GET | session.py | Public | GET convenience for logout |
| `/auth/login` | GET | session.py | Public | Serve login page |
| `/login` | GET | session.py | Public | Duplicate of /auth/login GET |

#### D. Utility Endpoints

| Path | Method | Module | Auth | Purpose |
|------|--------|--------|------|---------|
| `/health` | GET | main.py (inline) | Public | Health check |

#### E. Unregistered (Dead Code)

| Path | Method | Module | Auth | Purpose |
|------|--------|--------|------|---------|
| `/request-logs` | GET | request_log/routes.py | Admin token | List request/response logs |
| `/request-logs/{transaction_id}` | GET | request_log/routes.py | Admin token | Get transaction detail |

---

## 2. Issues and Observations

### 2.1 Inconsistent Navigation

**This is the biggest problem.** Each UI page has its own ad-hoc navigation, and no two pages share the same nav structure:

| Page | Nav pattern |
|------|-------------|
| `/` (landing) | Full sitemap, light theme — completely different design from all other pages |
| `/activity/monitor` | Only "Sign Out" link |
| `/debug/diff` | Only "Sign Out" link |
| `/policy-config` | "Activity Monitor", "Diff Viewer", "Sign Out" |
| `/credentials` | "Home", "Activity Monitor", "Policy Config", "Sign Out" |
| `/history` | Only "Sign Out" link |
| `/history/session/{id}` | "← Back to sessions", "Live View", "Export", "Sign Out" |
| `/conversation/live/{id}` | "← Sessions", "Export", "Sign Out" |
| `/client-setup` | "← Back to gateway", footer with links |

**Result**: Users frequently get "trapped" in a page with no way to navigate to related pages. For example, from `/activity/monitor` you can only sign out — you can't get to history, policy config, or credentials without manually editing the URL or going back to `/`.

### 2.2 Inconsistent URL Patterns

- **UI pages use mixed conventions**: `/activity/monitor`, `/debug/diff`, `/policy-config`, `/credentials`, `/history`, `/client-setup`, `/conversation/live/{id}`
  - Some use path segments (`/activity/monitor`)
  - Some use hyphenated slugs (`/policy-config`, `/client-setup`)
  - `/debug/diff` is a UI page living under the `/debug` API namespace
- **API sub-routes use `/api/` in one module but not others**: `/history/api/sessions` has `/api/` prefix, but `/debug/calls` and `/admin/policy/current` don't
- **No clear separation between API and UI routes** in URL structure

### 2.3 Route Namespace Collisions

- **`/debug/diff`** (UI page, in ui/routes.py) lives under `/debug/*` namespace, but `/debug/calls` and `/debug/calls/{id}/diff` (API endpoints) are in debug/routes.py. The UI page and the API share the `/debug` prefix but are defined in different modules.
- **`/auth/login`** is served by two different router registrations (session_router and login_page_router), with `/login` as a convenience duplicate. The login page endpoint is registered twice.

### 2.4 Unregistered Module

The `request_log/` module has a complete router with models, service, and routes, but is **not included in main.py**. This is either dead code or an incomplete feature. The migration file `migrations/008_add_request_logs_table.sql` exists, suggesting it was partially built.

### 2.5 Inconsistent Design Language

- **Landing page** (`/`): Light theme (#f5f5f5 background), standard web fonts, blue accent
- **All other pages**: Dark theme (#0a0a0a or similar), system fonts, green/amber accents
- The landing page looks like it belongs to a different application

### 2.6 Auth Patterns

Two auth mechanisms are used but applied inconsistently to UI pages:

| Mechanism | Used by |
|-----------|---------|
| `verify_admin_token` (API key in header) | All API endpoints, `/activity/stream` |
| `check_auth_or_redirect` (session cookie OR API key, redirects to login) | All UI pages |

This is actually well-designed — API endpoints require explicit tokens, UI pages support session cookies with login redirect. The implementation is consistent.

### 2.7 Missing Features / Gaps

- **No request log UI**: The `request_log` module has API routes but no HTML page, and the routes aren't even registered
- **No UI for debug/calls API**: The `/debug/calls` list endpoint has no HTML UI — you can only access it via API or through the diff viewer which requires knowing a call_id
- **No unified "dashboard" page**: The landing page is a static sitemap, not a live overview
- **No breadcrumb navigation**: Deep pages (conversation detail → session → history) have back links but no breadcrumb trail

---

## 3. Consolidation Strategy

### 3.1 URL Namespace Convention

Adopt a clear two-tier convention:

```
/api/...        — JSON API endpoints (consumed by UI pages and external tools)
/...            — UI pages (HTML, served to browsers)
/auth/...       — Authentication (keep as-is, works well)
/health         — Keep at root (standard convention)
/v1/...         — Proxy pass-through (out of scope)
```

#### Proposed API Reorganization

| Current Path | Proposed Path | Change? |
|-------------|---------------|---------|
| `/admin/policy/current` | `/api/admin/policy/current` | Prefix with `/api` |
| `/admin/policy/set` | `/api/admin/policy/set` | Prefix with `/api` |
| `/admin/policy/list` | `/api/admin/policy/list` | Prefix with `/api` |
| `/admin/models` | `/api/admin/models` | Prefix with `/api` |
| `/admin/test/chat` | `/api/admin/test/chat` | Prefix with `/api` |
| `/admin/auth/config` | `/api/admin/auth/config` | Prefix with `/api` |
| `/admin/auth/credentials` | `/api/admin/auth/credentials` | Prefix with `/api` |
| `/debug/calls` | `/api/debug/calls` | Prefix with `/api` |
| `/debug/calls/{id}` | `/api/debug/calls/{id}` | Prefix with `/api` |
| `/debug/calls/{id}/diff` | `/api/debug/calls/{id}/diff` | Prefix with `/api` |
| `/history/api/sessions` | `/api/history/sessions` | Move from `/history/api/` to `/api/history/` |
| `/history/api/sessions/{id}` | `/api/history/sessions/{id}` | Move from `/history/api/` to `/api/history/` |
| `/history/api/sessions/{id}/export` | `/api/history/sessions/{id}/export` | Move |
| `/activity/stream` | `/api/activity/stream` | Prefix with `/api` |
| `/request-logs` | `/api/request-logs` | Register + prefix with `/api` |
| `/request-logs/{id}` | `/api/request-logs/{id}` | Register + prefix with `/api` |

**Backward compatibility**: Add redirect middleware or dual-register routes during a transition period. Since this is an internal admin API (not a public contract), a clean break is probably fine — just update the JS in all HTML templates at the same time.

#### Proposed UI Page Paths

Keep UI pages at the root level with consistent naming:

| Current Path | Proposed Path | Change? |
|-------------|---------------|---------|
| `/` | `/` | Keep |
| `/activity/monitor` | `/activity` | Simplify |
| `/debug/diff` | `/diffs` | Move out of `/debug` namespace |
| `/policy-config` | `/policies` | Shorter, noun-based |
| `/credentials` | `/credentials` | Keep |
| `/conversation/live/{id}` | `/conversations/{id}` | Simplify |
| `/history` | `/history` | Keep |
| `/history/session/{id}` | `/history/{id}` | Simplify |
| `/client-setup` | `/setup` | Shorter |
| `/login` | `/login` | Keep |

### 3.2 Module Organization

Consider restructuring route files to match the URL convention:

```
src/luthien_proxy/
  api/                    # All JSON API routes
    admin_routes.py       # /api/admin/*
    debug_routes.py       # /api/debug/*
    history_routes.py     # /api/history/*
    activity_routes.py    # /api/activity/stream
    request_log_routes.py # /api/request-logs/*
  pages/                  # All HTML-serving routes
    routes.py             # All UI page routes (single file is fine)
  auth/                   # Authentication
    session.py            # /auth/*, /login
```

**Alternative (less disruptive)**: Keep current module structure but add `/api` prefix to all API routers. This requires changing only the router prefix declarations and the JavaScript `fetch()` URLs in HTML templates.

### 3.3 Register the Request Log Routes

Add to `main.py`:
```python
from luthien_proxy.request_log import routes as request_log_routes
app.include_router(request_log_routes.router)
```

And build a UI page for it (this is likely already planned).

---

## 4. Unified UI Navigation Proposal

### 4.1 Problem

Each page implements its own header with hardcoded navigation links. Adding a new page requires editing every existing page's header. Some pages have minimal navigation (just "Sign Out"), making them dead-ends.

### 4.2 Solution: Shared Navigation Component

Create a reusable nav bar that all dark-theme pages include. Two implementation approaches:

#### Option A: Alpine.js Component (Recommended)

Since the project already uses Alpine.js in several pages, create a standalone nav component that pages include via a `<script>` tag and Alpine directive.

**File**: `/static/nav.js`
```javascript
// Shared navigation component for all Luthien UI pages
document.addEventListener('alpine:init', () => {
    Alpine.data('luthienNav', () => ({
        currentPath: window.location.pathname,
        links: [
            { href: '/activity', label: 'Activity', icon: '⚡' },
            { href: '/history', label: 'History', icon: '📜' },
            { href: '/policies', label: 'Policies', icon: '⚙️' },
            { href: '/diffs', label: 'Diffs', icon: '🔍' },
            { href: '/credentials', label: 'Credentials', icon: '🔑' },
            { href: '/setup', label: 'Setup', icon: '🔧' },
        ],
        isActive(href) {
            return this.currentPath.startsWith(href);
        }
    }));
});
```

**Usage in each page**:
```html
<nav x-data="luthienNav">
    <a href="/" class="nav-brand">Luthien</a>
    <template x-for="link in links">
        <a :href="link.href"
           :class="{ 'active': isActive(link.href) }"
           x-text="link.label"></a>
    </template>
    <a href="/auth/logout" class="nav-signout">Sign Out</a>
</nav>
<script src="/static/nav.js"></script>
```

**Pros**:
- Adding a new page = adding one entry to the `links` array in `nav.js`
- No server-side changes needed to update navigation
- Active page highlighting comes free
- Consistent styling via shared CSS

**Cons**:
- Requires Alpine.js on every page (some pages might not currently use it)

#### Option B: Server-Side HTML Include (via fetch)

For pages that don't use Alpine.js, inject the nav HTML via a fetch at page load:

**File**: `/static/nav.html` (fragment)
```html
<nav class="luthien-nav">
    <a href="/" class="nav-brand">Luthien</a>
    <a href="/activity">Activity</a>
    <a href="/history">History</a>
    <a href="/policies">Policies</a>
    <a href="/diffs">Diffs</a>
    <a href="/credentials">Credentials</a>
    <a href="/setup">Setup</a>
    <div class="nav-spacer"></div>
    <a href="/auth/logout">Sign Out</a>
</nav>
```

Each page includes:
```html
<div id="nav-container"></div>
<script>
fetch('/static/nav.html')
    .then(r => r.text())
    .then(html => {
        document.getElementById('nav-container').innerHTML = html;
        // Highlight active link
        document.querySelectorAll('.luthien-nav a').forEach(a => {
            if (location.pathname.startsWith(a.getAttribute('href')) && a.getAttribute('href') !== '/') {
                a.classList.add('active');
            }
        });
    });
</script>
```

**Pros**: No JS framework dependency
**Cons**: Flash of unstyled content on load, extra HTTP request

#### Option C: Server-Side Template Injection (via Jinja2 or string replace)

Add a `{{NAV}}` placeholder in each HTML file and have the route handler inject the nav HTML server-side (similar to how `/client-setup` already injects `{{BASE_URL}}`).

**Pros**: No flash of content, no JS dependency
**Cons**: Requires modifying each route handler, more server code

### 4.3 Recommendation

**Use Option A (Alpine.js component)** for all authenticated pages. The project already depends on Alpine.js and it provides the cleanest developer experience.

For the two public pages (`/` landing and `/client-setup`), these don't need the same nav since they serve different purposes — the landing page IS the navigation, and client-setup has its own flow.

### 4.4 Nav Bar Design

Match the existing dark theme:

```
┌──────────────────────────────────────────────────────────────────┐
│  Luthien    Activity  History  Policies  Diffs  Credentials     │
│                                                     [Sign Out]  │
└──────────────────────────────────────────────────────────────────┘
```

- Horizontal top bar, fixed position
- Background: `#141414` with `#262626` bottom border
- Text: `#888` default, `#e5e5e5` hover, `#4ade80` active
- "Luthien" brand links to `/`
- "Sign Out" right-aligned
- Active page indicated by bottom border or text color
- Compact: single line, no sub-menus

### 4.5 Shared CSS

Create `/static/nav.css` with the nav bar styles, loaded by each page. This avoids duplicating CSS across 9 HTML files.

---

## 5. Prioritized Action Items

1. **Quick win: Fix landing page theme** — Update `/` (index.html) to dark theme matching all other pages
2. **Quick win: Register request_log routes** — Add to main.py, they're already built
3. **Build shared nav component** — Alpine.js component + CSS file
4. **Retrofit nav into existing pages** — Replace ad-hoc header-links with shared component
5. **Add `/api` prefix** — Move all JSON API endpoints under `/api/` namespace
6. **Simplify UI paths** — `/activity/monitor` → `/activity`, `/debug/diff` → `/diffs`, etc.
7. **Build request log UI page** — Missing UI for the request_log API
8. **Build debug calls list UI** — Currently no way to browse calls without knowing a call_id
9. **Remove /login duplicate** — `/auth/login` GET is sufficient, `/login` adds confusion

---

## Summary Statistics

| Category | Count |
|----------|-------|
| Total non-proxy routes | 31 registered + 2 unregistered |
| UI pages | 11 (including login) |
| JSON API endpoints | 17 |
| Auth endpoints | 5 |
| Utility endpoints | 1 |
| Unique HTML template files | 9 |
| Pages with no nav except Sign Out | 3 (activity monitor, diff viewer, history list) |
| Unregistered routes | 2 (request-logs) |
