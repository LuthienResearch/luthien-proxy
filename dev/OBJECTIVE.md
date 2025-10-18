# Objective: Integrated Architecture with Network-Ready Control Plane

## Goal

Replace the LiteLLM proxy + separate control plane architecture with an integrated FastAPI service that uses LiteLLM as a library, while maintaining a clean interface that allows the control plane logic to be separated and networked in the future.

## Key Requirements

1. **Network-Ready Interface**: Design a clear boundary between web frontend and control logic
   - Control logic should be callable both in-process and over network
   - Interface should be protocol-agnostic (HTTP, gRPC, etc. could be added later)
   - Keep deployment flexibility: single process for simplicity, distributed for scale

2. **Feature Parity**: Maintain all existing functionality
   - Activity stream UI and real-time updates
   - Database logging (Prisma/PostgreSQL)
   - Debug UI and conversation traces
   - Redis caching
   - All existing policy capabilities

3. **Clean Policy Abstraction**: PolicyHandler interface
   - `apply_request_policies`: pre-request validation/modification
   - `apply_response_policy`: post-response handling
   - `apply_streaming_chunk_policy`: stream control with bidirectional flow
   - Port existing policies to new interface

4. **Parallel Structure**: Build alongside existing system
   - New code in `src/luthien_proxy/v2/` or similar
   - Can compare both approaches
   - Safe rollback path

## Acceptance Criteria

- [ ] Control plane interface defined with clear boundaries for future network separation
- [ ] Core proxy working with OpenAI and Anthropic format endpoints
- [ ] At least one existing policy ported and working with new interface
- [ ] Activity stream integrated and displaying real-time data
- [ ] Database logging operational
- [ ] Debug UI accessible and functional
- [ ] Docker compose configuration updated
- [ ] All existing tests passing
- [ ] New architecture documented with migration path

## Non-Goals (out of scope)

- Actually implementing network separation (just designing for it)
- Removing old architecture (parallel for now)
- Migrating all policies immediately (incremental is fine)
