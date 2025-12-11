# User Story 2: Platform Team - Organization-Wide Visibility and Escalation

## Persona

**Jordan** - A platform engineer responsible for AI safety across a 200-person engineering org.

## Context

Jordan needs per-user dashboards for team leads, automatic escalation of high-risk events, and support for the various ways engineers use AI tools—including image/file attachments and parallel query patterns.

## Story

> As Jordan, I want dashboards showing AI usage per user and per conversation, with automatic escalation of high-importance events, so that team leads have visibility into their team's AI usage and security incidents are surfaced immediately.

## Scenario

1. Jordan deploys the proxy with the escalation policy enabled
2. The policy defines escalation tiers:
   - **INFO**: Normal operations, logged but no alert
   - **WARNING**: Blocked operations, logged + visible in dashboard
   - **CRITICAL**: Attempted access to production, secrets, or PII → immediate Slack/PagerDuty alert
3. Engineer Pat uploads a screenshot to Claude Code asking "What's wrong with this error?"
4. The proxy:
   - Extracts and logs the image attachment metadata
   - Passes the image through to Claude (media attachment support)
   - Stores the conversation event with attachment reference
5. Engineer Casey runs a "resampling" query—same prompt sent 3 times in parallel for comparison
6. The proxy:
   - Recognizes parallel queries with same content as resampling pattern
   - Tracks all 3 as part of same logical request
   - Applies policy once, not 3 times (avoids redundant judge calls)
7. Engineer Dana accidentally pastes AWS credentials into a prompt
8. The proxy:
   - Detects secret pattern in request
   - Logs as **CRITICAL** event
   - Fires webhook to Slack: "Credentials detected in AI prompt - User: dana@company.com"
   - Blocks the request with injected message explaining the block
9. Jordan views the **Per-User Dashboard** at `localhost:8000/dashboard/users/dana`
   - Sees Dana's conversation history, intervention rate, and this critical event
   - Can drill into the specific conversation
10. Team lead Morgan views their team's dashboard
    - Sees aggregate stats: requests/day, block rate, top triggered rules
    - Identifies that the "no secrets" rule is triggering frequently → schedules team training

## Acceptance Criteria

- [ ] Per-user dashboard showing conversation history and intervention stats
- [ ] Per-conversation drill-down with full message history
- [ ] Escalation tiers (INFO/WARNING/CRITICAL) with configurable actions
- [ ] Webhook integration for critical events (Slack, PagerDuty, etc.)
- [ ] Media attachment support (images, files) with metadata logging
- [ ] Parallel/resampling queries are tracked as logical groups
- [ ] Team-level aggregate dashboards for leads
- [ ] Audit log export with user attribution

## Required Features

### Core Infrastructure

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-5sr` | Conversation context tracking across requests | open | P1 |
| `luthien-proxy-kxh` | Media attachment support | open | P2 |
| `luthien-proxy-822` | Parallel query (resampling) support | open | P2 |

### Dashboards & UI

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-ay5` | Per-user and per-conversation dashboards | open | P2 |
| `luthien-proxy-edl` | Conversation Viewer UI | open | P1 |

### Alerting & Escalation

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-aai` | Escalation tiers with webhook alerts | open | P2 |

### Message Injection

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-fsb` | Message injection into response stream | open | P1 |

### Infrastructure (Existing)

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-09n` | Document production deployment best practices | open | P2 |
| `luthien-proxy-037` | Add resource limits to docker-compose.yaml | open | P2 |
| `luthien-proxy-7tj` | Add Prometheus metrics endpoint | open | P2 |

## Technical Touchpoints

- `ui/dashboard`: Per-user and per-team dashboards
- `storage/`: User attribution and session tracking
- `observability/escalation`: Tiered logging and webhook dispatch
- `gateway_routes.py`: Media attachment handling and pass-through
- `policies/`: Parallel query detection and deduplication
- Admin API: Dashboard data endpoints

## Implementation Status

**Overall Progress**: Not Started

### Phase 1: User Attribution
- [ ] Extract user identity from request headers/auth
- [ ] Store user ID with all conversation events
- [ ] Add user-based query capabilities to storage layer

### Phase 2: Media Attachments
- [ ] Detect and extract attachment metadata from requests
- [ ] Pass attachments through to backend LLM
- [ ] Store attachment references (not content) in event log

### Phase 3: Resampling Detection
- [ ] Implement content-based request deduplication window
- [ ] Group parallel requests into logical request sets
- [ ] Apply policy once per logical request

### Phase 4: Escalation Framework
- [ ] Define escalation tier schema
- [ ] Implement webhook dispatcher
- [ ] Add Slack/PagerDuty integrations

### Phase 5: Dashboards
- [ ] Design dashboard data models
- [ ] Implement per-user dashboard API endpoints
- [ ] Build dashboard UI components
- [ ] Add team aggregation views

## Dependencies

```
luthien-proxy-5sr (Conversation context tracking)
    └── luthien-proxy-ay5 (Per-user dashboards)
    └── luthien-proxy-edl (Conversation Viewer UI)

luthien-proxy-aai (Escalation tiers)
    └── luthien-proxy-fsb (Message injection) [for block notifications]
```

## Notes

- User identity should support multiple auth mechanisms (API key mapping, JWT, header injection)
- Resampling detection window should be configurable (default: 5 seconds)
- Webhook payloads should be configurable per escalation tier
- Consider rate limiting on webhooks to prevent alert storms
- Dashboard should support role-based access (user sees own data, lead sees team)
