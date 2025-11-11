# Objective: Add request size limits to V2 gateway

## Goal

Implement request size validation to prevent DoS attacks via oversized payloads.

## Scope

- Add configurable `MAX_REQUEST_SIZE` environment variable (default: 10MB)
- Create middleware to validate request body size before JSON parsing
- Log and reject oversized requests with appropriate error response
- Add unit and integration tests

## Acceptance Criteria

- [ ] `MAX_REQUEST_SIZE` env var documented in `.env.example`
- [ ] Middleware validates request size before body parsing
- [ ] Oversized requests return 413 Payload Too Large with clear message
- [ ] Rejected requests are logged with size and transaction ID
- [ ] Unit tests cover middleware validation logic
- [ ] Integration test verifies end-to-end rejection
- [ ] All existing tests pass
- [ ] Code formatted and type-checked
