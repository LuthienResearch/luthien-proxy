# Current Objective

Implement session-based login for browser access to admin/debug UIs.

## Acceptance Criteria

- [x] `/login` page that accepts ADMIN_API_KEY
- [x] Session cookie set on successful login
- [x] Protected UI pages redirect to login when unauthenticated
- [x] Sign out link on all protected pages
- [x] API endpoints still accept Bearer token (backwards compatible)
- [x] Tests for session auth logic
- [x] All dev checks pass
