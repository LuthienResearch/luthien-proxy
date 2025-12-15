# Current Objective

Implement structured span hierarchy for request processing (`luthien-proxy-a0r`)

## Acceptance Criteria

- [ ] Root `transaction_processing` span wraps all processing
- [ ] Four sibling child spans: `process_request`, `send_upstream`, `process_response`, `send_to_client`
- [ ] Policy hooks can create arbitrary nested spans
- [ ] Span attributes include: `transaction_id`, `model`, `stream`, `client_format`
- [ ] Span events log key transitions without creating span overhead
- [ ] Grafana/Tempo can visualize the pipeline phases clearly (screenshot in PR)
- [ ] Works for both streaming and non-streaming requests
