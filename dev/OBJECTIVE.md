# Objective

Remove all OpenAI/GPT references from codebase docs, config files, and dead code.

## Description

PR #491 cleaned up the README. This pass audits the remaining codebase: config examples, dev docs, deploy docs, startup scripts, and .env.example. References to `OpenAI` as an API format name (e.g., `OpenAIPolicyInterface`) are architectural and stay. References to `OPENAI_API_KEY`, GPT model names in examples, and OpenAI-as-a-provider documentation are removed or replaced with Anthropic/Claude equivalents.

## Approach

1. **Config files** (.env.example, policy_config.yaml, railway.toml): Replace GPT model examples with Claude equivalents, remove OPENAI_API_KEY references
2. **Deploy/ops docs** (deploy/README.md, docs/standalone-container.md, docker/Dockerfile.standalone): Remove OPENAI_API_KEY from env var tables and examples
3. **Dev docs** (dev-README.md, dev/OBSERVABILITY_DEMO.md): Replace GPT model examples with Claude equivalents
4. **Startup scripts** (start_gateway.sh, quick_start.sh, quick_start_standalone.sh): Remove OPENAI_API_KEY checks
5. **Source code**: Update model string examples in Field descriptions from gpt-4o to claude equivalents. Leave functional GPT detection logic in tool_call_judge_utils.py (it handles real GPT models). Leave sentry scrubbing of openai_api_key (defense in depth).
6. **Dev context docs**: Leave architectural "OpenAI format" references that describe the dual-format pipeline. Remove/update standalone GPT model examples.

## What stays (functional, not dead references)

- `OpenAIPolicyInterface` and all "OpenAI format" architectural references — this is the name of the format pipeline
- `tool_call_judge_utils.py` GPT model detection logic — handles real GPT models from LiteLLM
- `sentry.py` `openai_api_key` scrubbing — defense in depth
- Test files — out of scope per card instructions

## Test Strategy

- Unit tests: Run existing test suite to confirm no breakage
- No new tests needed — this is a docs/config cleanup

## Acceptance Criteria

- [ ] No OPENAI_API_KEY references in config, deploy docs, or startup scripts
- [ ] No GPT model examples in config/docs (replaced with Claude equivalents)
- [ ] Functional OpenAI format translation code untouched
- [ ] dev_checks passes
- [ ] All existing tests pass

## Tracking

- Trello: https://trello.com/c/jiKgyiKE
- Branch: worktree-remove-openai-refs
- PR: (filled after creation)
