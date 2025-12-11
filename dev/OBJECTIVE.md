# Current Objective

Unify OpenAI and Anthropic endpoint processing (`luthien-proxy-en1`)

## Acceptance Criteria

- [ ] Single `process_llm_request()` function handles both endpoints
- [ ] Endpoint handlers are <10 lines each (just delegation)
- [ ] Root `transaction_processing` span wraps all processing
- [ ] Four sibling child spans: `process_request`, `send_upstream`, `process_response`, `send_to_client`
- [ ] Format conversion only at ingress/egress boundaries
- [ ] All existing tests pass
- [ ] Span attributes include: `call_id`, `model`, `stream`, `client_format`

## Reference

- [Story 5: Infrastructure](user-stories/05-infrastructure-observability-unification.md)
- Issue: `luthien-proxy-en1`
