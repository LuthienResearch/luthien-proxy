# Objective: Remove "v2" as a concept

Remove all references to "v2" from the codebase and consolidate into main package structure.

## Acceptance Criteria

- [ ] All code from `src/luthien_proxy/v2/*` is moved to `src/luthien_proxy/*`
- [ ] All imports updated from `luthien_proxy.*` to `luthien_proxy.*`
- [ ] Route prefixes `/debug` ’ `/debug`, `/activity` ’ `/activity`, `/v2/static` ’ `/static`
- [ ] Test directories moved from `tests/**/v2/` to `tests/**/`
- [ ] All documentation updated to remove v2 references
- [ ] All tests pass via `./scripts/dev_checks.sh`
