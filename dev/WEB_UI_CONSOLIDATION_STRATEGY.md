# Web UI Consolidation Strategy

*Created: 2026-02-13*

## 1. Current State: Complete Endpoint Inventory

### Human-Facing HTML Pages (Browser UIs)

| # | URL Path | Method | Route Module | Auth | Purpose | Status |
|---|----------|--------|--------------|------|---------|--------|
| 1 | `/` | GET | `ui/routes.py` | Public | Landing page with links to all endpoints | Working |
| 2 | `/login` | GET | `session.py` (login_page_router) | Public | Login form page | Working |
| 3 | `/activity/monitor` | GET | `ui/routes.py` | Session cookie | Real-time activity stream viewer (SSE-powered) | Working |
| 4 | `/debug/diff` | GET | `ui/routes.py` | Session cookie | Side-by-side policy diff viewer (enter call_id) | Working |
| 5 | `/policy-config` | GET | `ui/routes.py` | Session cookie | Policy wizard: browse, activate, configure, and test policies | Working |
| 6 | `/deploy-instructions` | GET | `ui/routes.py` | Public | Step-by-step Claude Code + Luthien setup guide | Working |
| 7 | `/history` | GET | `history/routes.py` | Session cookie | Conversation session list (paginated) | Working |
| 8 | `/history/session/{id}` | GET | `history/routes.py` | Session cookie | Single session detail (message transcript) | Working |
| 9 | `/conversation/live/{id}` | GET | `ui/routes.py` | Session cookie | Live conversation viewer with real-time updates and policy diffs | New (untracked) |

### API Endpoints (JSON, used by UIs and external tools)

| # | URL Path | Method | Route Module | Auth | Purpose |
|---|----------|--------|--------------|------|---------|
| 10 | `/health` | GET | `main.py` (inline) | Public | Health check |
| 11 | `/activity/stream` | GET | `ui/routes.py` | Bearer token | SSE stream of all gateway activity (consumed by activity monitor) |
| 12 | `/debug/calls` | GET | `debug/routes.py` | Bearer token | List recent calls with event counts |
| 13 | `/debug/calls/{id}` | GET | `debug/routes.py` | Bearer token | All events for a specific call |
| 14 | `/debug/calls/{id}/diff` | GET | `debug/routes.py` | Bearer token | JSON diff of policy changes for a call |
| 15 | `/history/api/sessions` | GET | `history/routes.py` | Bearer token | List sessions (JSON, paginated) |
| 16 | `/history/api/sessions/{id}` | GET | `history/routes.py` | Bearer token | Session detail (JSON) |
| 17 | `/history/api/sessions/{id}/export` | GET | `history/routes.py` | Bearer token | Export session as markdown download |
| 18 | `/admin/policy/current` | GET | `admin/routes.py` | Bearer token | Current active policy info |
| 19 | `/admin/policy/set` | POST | `admin/routes.py` | Bearer token | Set/change the active policy |
| 20 | `/admin/policy/list` | GET | `admin/routes.py` | Bearer token | Discover available policy classes |
| 21 | `/admin/models` | GET | `admin/routes.py` | Bearer token | List available LLM models |
| 22 | `/admin/test/chat` | POST | `admin/routes.py` | Bearer token | Send test message through proxy pipeline |

### Auth Endpoints

| # | URL Path | Method | Route Module | Purpose |
|---|----------|--------|--------------|---------|
| 23 | `/auth/login` | GET | `session.py` | Login page (same as `/login`) |
| 24 | `/auth/login` | POST | `session.py` | Handle login form submission |
| 25 | `/auth/logout` | POST | `session.py` | Logout (clear cookie) |
| 26 | `/auth/logout` | GET | `session.py` | Logout via GET (convenience) |

### Gateway Endpoints (LLM proxy, not human-facing)

| # | URL Path | Method | Route Module | Purpose |
|---|----------|--------|--------------|---------|
| 27 | `/v1/chat/completions` | POST | `gateway_routes.py` | OpenAI-compatible proxy |
| 28 | `/v1/messages` | POST | `gateway_routes.py` | Anthropic Messages API proxy |

### Static File Mount

- `/static/*` — mounted via `StaticFiles` in `main.py`, serves CSS/JS/HTML assets

---

## 2. Issues Identified

### A. Duplicated Code

- **`_check_auth_or_redirect()`** is copy-pasted identically in both `ui/routes.py` (line 28) and `history/routes.py` (line 36). This should be a shared utility.

### B. Inconsistent Navigation

- **No shared nav bar.** Each page has its own ad-hoc navigation links. Users must go back to `/` (the landing page) to find other views.
- **Cross-links are incomplete.** For example:
  - `activity_monitor.html` has no links to other pages
  - `diff_viewer.html` has no links to other pages
  - `history_list.html` and `history_detail.html` only link to each other
  - `conversation_live.html` only links back to `/history`
  - `policy_config.html` links to activity monitor and diff viewer, but not to history
- **Landing page (`/`) has a different visual style** (light background, blue accents) than all other pages (dark theme, green/purple accents).

### C. Overlapping Functionality

- **History detail vs. Live conversation view**: `/history/session/{id}` and `/conversation/live/{id}` both show conversation content for a session. The live view adds real-time updates and policy diffs, making the static history detail partially redundant.
- **Debug diff viewer vs. History detail**: The diff viewer (`/debug/diff`) requires manually entering a call_id. The history detail page and live view could integrate diff viewing inline, making the standalone diff viewer less necessary.
- **Activity monitor vs. Live conversation view**: The activity monitor shows all gateway events globally. The live conversation view shows events for a single conversation. There's conceptual overlap but they serve different granularities.

### D. Inconsistent Auth Patterns

- HTML pages use session cookie auth (`_check_auth_or_redirect`)
- API endpoints use bearer token auth (`verify_admin_token`)
- This is correct behavior (browsers vs. API clients) but the duplicated `_check_auth_or_redirect` function adds maintenance burden.

### E. Confusing URL Structure

- `/debug/diff` is an HTML UI page but lives under `/debug/` alongside API endpoints
- `/activity/monitor` (UI) and `/activity/stream` (SSE API) are siblings, which is fine
- `/conversation/live/{id}` and `/history/session/{id}` are under different prefixes for related content
- Admin API is cleanly at `/admin/*` — good

### F. Login Page Duplication

- Login is served at both `/login` and `/auth/login` (via two separate routers). The `/login` convenience redirect works fine, but is an unnecessary duplication.

---

## 3. Proposed Consolidated Structure

### URL Redesign

Group all human-facing UI pages under a coherent structure:

```
/                              Landing page (keep as-is, update styling)
/login                         Login page (keep, remove /auth/login GET duplicate)
/monitor                       Real-time activity monitor (rename from /activity/monitor)
/history                       Session list (keep)
/history/{id}                  Session detail — merge live + static views (consolidate)
/policy                        Policy config wizard (rename from /policy-config)
/setup                         Deploy instructions (rename from /deploy-instructions)
/diff/{call_id}                Diff viewer (rename from /debug/diff, optional param)
```

### API URL Cleanup (lower priority)

Keep existing API URLs for backwards compatibility, but consider:
```
/api/activity/stream           SSE stream (rename from /activity/stream)
/api/debug/calls               Debug calls list (keep)
/api/debug/calls/{id}          Call events (keep)
/api/debug/calls/{id}/diff     Call diff (keep)
/api/history/sessions          Session list (rename from /history/api/sessions)
/api/history/sessions/{id}     Session detail (rename from /history/api/sessions/{id})
/api/admin/policy/*            Policy management (rename from /admin/policy/*)
```

### Shared Navigation Component

Add a persistent nav bar to all authenticated pages:

```
[Luthien Proxy]  Monitor | History | Policy | Diff | Setup    [Logout]
```

This should be a shared HTML snippet or a JS-injected component to avoid duplicating nav markup across all static HTML files.

---

## 4. Implementation Roadmap

### Phase 1: Quick Wins (no URL changes, minimal risk)

1. **Extract `_check_auth_or_redirect` to shared module** — move to `auth.py` or a new `ui/auth_helpers.py`, import in both `ui/routes.py` and `history/routes.py`.

2. **Add shared nav bar** — create a small JS file (`/static/nav.js`) that injects a navigation bar into all authenticated pages. Each page includes `<script src="/static/nav.js"></script>` with a `data-active="monitor"` attribute to highlight the current page.

3. **Update landing page styling** — match the dark theme used by all other pages for visual consistency.

4. **Add cross-links to isolated pages** — add navigation links to `activity_monitor.html` and `diff_viewer.html` that currently have no way to navigate to other pages.

### Phase 2: View Consolidation (medium effort)

5. **Merge history detail + live conversation view** — the live conversation view (`/conversation/live/{id}`) is a superset of the static history detail (`/history/session/{id}`). Consolidate into a single view at `/history/session/{id}` that:
   - Shows the static conversation transcript by default
   - Adds a "Live" toggle that enables real-time updates via SSE
   - Includes inline policy diff viewing (eliminating need to go to separate diff viewer)

6. **Integrate diff viewing into history** — instead of a standalone `/debug/diff` page that requires manual call_id entry, add "View Diff" buttons next to each turn in the history detail view. Keep the standalone page for direct access but make it less prominent.

### Phase 3: URL Cleanup (breaking change, optional)

7. **Rename URLs** per the proposed structure above. Add redirects from old paths to new paths for a transition period.

8. **Consolidate API paths** under `/api/` prefix. Add redirects for backward compatibility.

### Phase 4: UX Polish

9. **Add search/filter to history list** — filter by session ID, model, date range.

10. **Add breadcrumbs** — show navigation path (e.g., History > Session abc123).

11. **Unify CSS** — extract shared theme variables into a common stylesheet.

---

## 5. Quick Wins Summary

These can be done immediately with minimal risk:

| Quick Win | Effort | Impact |
|-----------|--------|--------|
| Extract shared `_check_auth_or_redirect` | 15 min | Reduces code duplication |
| Add shared nav bar (JS component) | 1 hr | Major navigation improvement |
| Add links to activity_monitor.html | 10 min | Users can navigate away |
| Update landing page to dark theme | 30 min | Visual consistency |
| Add links to diff_viewer.html | 10 min | Users can navigate away |

---

## 6. Key Recommendations

1. **Start with the shared nav bar** — this single change dramatically improves discoverability and navigation. Every page becomes reachable from every other page.

2. **Merge live + static conversation views** — maintaining two separate views for the same content (with one being a strict superset) creates confusion. The live view should replace the static detail view.

3. **Don't break existing URLs yet** — URL renaming can wait. The nav bar solves the discoverability problem without breaking bookmarks or external links.

4. **Keep the landing page** — it serves as both a human-oriented index and a quick reference for API endpoints. Update its styling but keep the content.

5. **The diff viewer should become a feature, not a page** — inline diff viewing in the history/conversation view is more natural than a separate page that requires manual call_id entry.
