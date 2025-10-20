# Observability Architecture Proposal – Review Summary

## Key Feedback
- Proposed `request_traces` table duplicates before/after payloads already captured via the v1 `conversation_events` path; v2 still needs a single source of truth, but that pipeline has to live inside the new gateway rather than depend on LiteLLM callbacks.
- Synchronous writes from the FastAPI gateway (`await store_request_trace/update_request_trace`) would add two PostgreSQL round-trips to the hot path and conflict with our “fail fast” goal; v2 should enqueue persistence work onto an internal async queue so request handling stays non-blocking.
- Streaming coverage can’t lean on `async_post_call_streaming_hook` once LiteLLM is gone, so the architecture must spell out how the v2 streaming orchestrator buffers chunks, annotates policy decisions, and stores the final assembled response for diffing.
- Tempo queries can start with the Grafana proxy, but we should call out the coupling explicitly and eventually consider hitting Tempo’s HTTP API directly.
- Documentation needs to reference the correct Prisma project (`prisma/control_plane/schema.prisma`), not a non-existent top-level file.

## Revised Plan Snapshot
- Rebuild the conversation-event pipeline inside v2: generate the `call_id` at the gateway boundary, enqueue stage-specific records (request_original, request_final, response_original, response_final), and persist them asynchronously so consumers can reconstruct timelines by `call_id` without unpacking blobs.
- Guarantee the `call_id` lifecycle: generate once at the gateway boundary, attach to spans/events immediately, and assert its presence whenever we enqueue persistence work.
- Focus new work on the diff/debug endpoint that reads from the stage-specific records, computes structured diffs, and links to Tempo via `luthien.call_id`. Add Grafana panels that point to the custom view instead of building a second dashboarding stack.
- Keep payload storage in Postgres (saner retention, structured queries) while leaving timing data in Tempo/Loki; this balances “use proven infra” with the bespoke needs of policy debugging.
- Time estimate drops to roughly 5–8 hours (diff UI + queries + docs/tests) because we skip the new storage phase and associated migrations.
