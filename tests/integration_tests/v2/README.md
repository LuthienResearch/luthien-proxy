# V2 Integration Tests

These tests verify the PolicyOrchestrator integration with real LLM backends.

They directly instantiate the orchestrator (using policy_orchestrator_old) and verify:
- Request processing through policies
- Streaming response handling
- Content transformation (uppercase policy)
- Tool call handling

Note: These currently test the **deprecated** policy_orchestrator_old.
They should eventually be updated to test the new orchestrator or removed.

These are **integration tests**, not E2E tests - they don't test the HTTP gateway.
