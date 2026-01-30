# Objective: Fix litellm import error in Docker

Remove fragile import of `OpenAIChatCompletionFinishReason` from `litellm.types.utils` which breaks Docker startup when a newer litellm version is installed.

## Acceptance Criteria

- [ ] Gateway starts successfully in Docker
- [ ] No import errors related to litellm types
- [ ] Type checking passes (pyright)
- [ ] Unit tests pass
