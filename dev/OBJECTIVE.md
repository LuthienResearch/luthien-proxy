# Objective: Split APIs

Abandon the common internal format in the proxy; handle Anthropic and OpenAI requests/responses independently.

## Acceptance Criteria

- [ ] Anthropic client → Anthropic policy → Anthropic SDK → Anthropic response works e2e
- [ ] NoOp policy validates infrastructure
- [ ] AllCaps policy validates content modification
- [ ] Unused conversion code deleted
- [ ] OpenAI route returns 501 (temporary)
