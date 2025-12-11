# Current Objective

Replace magic numbers with named constants for better maintainability.

## Magic Numbers Found

1. `policy_manager.py:294` - `blocking_timeout=10` (Redis lock timeout)
2. `main.py:82` - `[:20]` (database URL preview length)
3. `tool_call_judge_policy.py` - `[:200]` (tool arguments truncation, 4 instances)

## Acceptance Criteria

- [ ] Add new constants to `utils/constants.py`
- [ ] Replace all magic numbers with named constants
- [ ] Dev checks pass
- [ ] No functionality changes (purely refactor)
