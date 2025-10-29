# Objective: Pipeline Refactor - Clean Separation of Concerns

**Branch:** `transaction-recorder-and-early-spans`

**Status:** In Progress (Implementation Phase)

**Spec:** [pipeline-refactor-spec-v4-FINAL.md](./pipeline-refactor-spec-v4-FINAL.md)

## Goal

Refactor the V2 pipeline to achieve clean separation of concerns with:
- TransactionRecord for observability (separate streaming/non-streaming paths)
- PolicyOrchestrator coordinating flow between components
- LLMClient abstracting backend calls
- StreamingResponseContext with ingress/egress access for policies
- Correct streaming termination (feed_complete signal pattern)
- Proper non-streaming recording (no data loss)

## Acceptance Criteria

- [ ] All existing tests pass
- [ ] New components have >90% test coverage
- [ ] OpenAI + Anthropic endpoints work with orchestrator
- [ ] Streaming responses correctly flush chunks from on_stream_complete
- [ ] Non-streaming responses preserve full message content and metadata
- [ ] Observability events correctly emitted for both paths
- [ ] No API mismatches with existing components

## Implementation Phases

1. **Phase 1:** Extend existing components (StreamState, StreamingChunkAssembler)
2. **Phase 2:** Create new components (LLMClient, Context, TransactionRecord)
3. **Phase 3:** Implement PolicyOrchestrator
4. **Phase 4:** Integrate into gateway routes
5. **Phase 5:** Update policies to new API

---
