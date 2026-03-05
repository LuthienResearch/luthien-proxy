Fix policy statelessness: document the singleton invariant, rename `get_policy_state`→`get_request_state`, and tighten `freeze_configured_state` to catch private mutable attrs.

Acceptance:
- CLAUDE.md has a "Policy Architecture" section documenting the statelessness invariant
- BasePolicy and AnthropicPolicyIOProtocol docstrings document the contract
- All `get_policy_state`/`pop_policy_state` renamed to `get_request_state`/`pop_request_state`
- `freeze_configured_state` validates private mutable attrs too
- Existing private mutable config attrs converted to immutable equivalents (tuple, frozenset)
- All tests pass
- Scratchpad docstrings de-emphasized (it's unused by any policy)
