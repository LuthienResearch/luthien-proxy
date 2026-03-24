---
category: Refactors
pr: 421
---

**Unify policy interface to hooks-only**: Replace dual `run_anthropic`/hooks execution model with hooks as the sole interface. Eliminates ~660 lines of duplicate logic and the bug class from PR #409.
  - Remove unused `MultiParallelPolicy`
  - `AnthropicExecutionInterface` protocol now defines 4 hook methods instead of `run_anthropic`
  - Executor owns backend I/O; policies only implement hooks
