# Track A: Multi-provider Passthrough Routes

Adds `/openai/{path}`, `/gemini/{path}`, and `/anthropic/{path}` passthrough routes to the gateway.

## Security fixes
- **Open proxy closed**: `/openai` and `/gemini` routes now require strict `CLIENT_API_KEY` match (regardless of global `AUTH_MODE`)
- **Body size limit**: Enforces `MAX_REQUEST_PAYLOAD_BYTES` on passthrough requests
- **Lifespan-managed clients**: httpx clients are now created/closed via FastAPI lifespan (no resource leaks)
- **Hop-by-hop header stripping**: Response headers `transfer-encoding`, `set-cookie`, `server`, etc. are stripped before forwarding to clients

## Database
- Migration 019: adds `agent` column to `request_logs` table

**Depends on PR-A** (httpx-sse dependency).
