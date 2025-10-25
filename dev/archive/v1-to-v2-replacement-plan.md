# V1 to V2 Complete Replacement Plan

**Date**: 2025-10-22
**Status**: In Progress
**Goal**: Completely replace V1 architecture with V2, validating architectural soundness via ToolCallJudge implementation

## Context

V2 architecture is complete and working:
- Integrated FastAPI + LiteLLM (single process vs V1's two services)
- Queue-based policy interface (cleaner than V1's iterator-based hooks)
- OpenTelemetry observability built-in
- Better separation of concerns

V1 will be completely deleted - no legacy support, no backwards compatibility. Git history is our archive.

## Strategy

**Two-phase approach:**
1. **Validate V2**: Port ToolCallJudge (the MVP actually-useful policy) to prove V2 can handle complex logic
2. **Nuclear Replacement**: Delete V1, flatten V2 namespace, update all references

This de-risks the migration - we validate architectural soundness before burning bridges.

## Phase 1: Validate V2 Architecture (3-4 hours)

### Port ToolCallJudge to V2

**Why this policy?**
- Real complexity: streaming buffering, LLM judge calls, decision making
- Actually useful (unlike demo policies)
- Tests whether v2's queue-based streaming can handle production workloads

**Implementation mapping:**

| V1 Feature | V2 Implementation |
|-----------|------------------|
| Inherit from ToolCallBufferPolicy | Implement directly in one policy class |
| Buffer chunks via iterator | Use `incoming` queue + accumulator |
| Detect tool call completion | Same logic: check `finish_reason == "tool_calls"` |
| Call judge LLM | Same: `acompletion()` with judge prompt |
| Block/modify response | Write to `outgoing` queue (block msg or buffered chunks) |
| Event emission | Use `context.emit()` instead of `_emit_policy_event()` |
| Database logging | Use storage layer via context |

**Files to create:**
- `src/luthien_proxy/v2/policies/tool_call_judge.py` - Main implementation
- `tests/unit_tests/v2/policies/test_tool_call_judge.py` - Unit tests
- `tests/integration_tests/v2/test_tool_call_judge_integration.py` - Integration tests

**Acceptance criteria:**
- [x] ToolCallJudge v2 implementation complete
- [x] Unit tests passing (judge logic, blocking, passthrough) - 18/18 passing
- [ ] Integration test with actual LLM call and judge decision
- [x] Handles both streaming and non-streaming responses
- [x] Events emitted correctly via context
- [x] No v1 dependencies or imports
- [x] Queue draining behavior validated (shutdown queues drain before returning empty)

## Phase 2: Nuclear Replacement (2-3 hours)

### 2.1 Delete V1 Code

**Remove services from docker-compose.yaml:**
- `litellm-proxy` service (lines 56-89)
- `control-plane` service (lines 90-120)
- `control-plane-migrations` service (lines 38-54)

**Delete v1 source directories:**
```bash
rm -rf src/luthien_proxy/control_plane/
rm -rf src/luthien_proxy/proxy/
rm -rf src/luthien_proxy/policies/
```

**Delete v1 config files:**
- `config/litellm_callback.py`
- `config/debug_callback.py`
- `config/unified_callback.py`
- `config/replay_callback.py`

**Delete v1 test directories:**
```bash
rm -rf tests/unit_tests/control_plane/
rm -rf tests/unit_tests/proxy/
rm -rf tests/unit_tests/policies/
rm -rf tests/integration_tests/control_plane/
```

### 2.2 Flatten V2 Namespace

**Move v2 to main package location:**
```bash
# Move all v2 subdirectories up one level
mv src/luthien_proxy/v2/* src/luthien_proxy/
rmdir src/luthien_proxy/v2/

# Move v2 tests up
mv tests/unit_tests/v2/* tests/unit_tests/
rmdir tests/unit_tests/v2/
mv tests/integration_tests/v2/* tests/integration_tests/
rmdir tests/integration_tests/v2/
mv tests/e2e_tests/test_v2_api_compatibility.py tests/e2e_tests/test_api_compatibility.py
```

**Update all imports:**
- Find: `from luthien_proxy.v2`
- Replace: `from luthien_proxy`
- Files: All Python files in `src/`, `tests/`, `scripts/`, `config/`

### 2.3 Update Docker Configuration

**Rename and configure v2-gateway as primary:**

In `docker-compose.yaml`:
1. Rename `v2-gateway` service to `proxy`
2. Remove `profiles: ["observability"]` from proxy service
3. Update ports to use `${PROXY_PORT:-8000}:8000`
4. Keep observability stack under profile (optional)

**Update environment variables:**
- Remove v1-specific vars (CONTROL_PLANE_URL, LITELLM_PORT, etc.)
- Use v2 vars as primary (PROXY_API_KEY, PROXY_PORT, etc.)

### 2.4 Update Scripts

**scripts/quick_start.sh:**
- Start `proxy` service instead of `control-plane` + `litellm-proxy`
- Remove control-plane-migrations step (proxy handles its own migrations)
- Update service URLs in output

**scripts/test_proxy.py:**
- Replace with `scripts/test_v2_proxy.py` content
- Update default URL to `http://localhost:8000`

**Delete obsolete scripts:**
- `scripts/start_v2_gateway.sh` (functionality absorbed into quick_start)
- `scripts/test_v2_proxy.py` (merged into test_proxy.py)

### 2.5 Update Documentation

**README.md:**
- Remove V1 architecture description
- Update quick start to reflect single `proxy` service
- Update endpoints section (remove control-plane URLs)
- Simplify architecture section (no more "thin proxy + control plane")
- Update observability section to reflect integrated approach

**CLAUDE.md:**
- Update "Purpose & Scope" to describe v2 architecture
- Update docker service names (`proxy` instead of `control-plane` + `litellm-proxy`)
- Update build/test commands
- Update project structure section

**.env.example:**
- Remove v1 variables (CONTROL_PLANE_URL, CONTROL_PLANE_PORT, LITELLM_PORT, etc.)
- Promote v2 variables as primary
- Simplify comments (no need to explain v1 vs v2)

### 2.6 Update Configuration Files

**Rename v2_config.yaml to luthien_config.yaml:**
```bash
mv config/v2_config.yaml config/luthien_config.yaml
```

**Delete litellm_config.yaml:**
- V2 doesn't use LiteLLM proxy, so no litellm_config needed
- Model configuration now in luthien_config.yaml

### 2.7 Testing & Validation

**Run full test suite:**
```bash
./scripts/dev_checks.sh
```

**Fix any broken tests:**
- Update test fixtures for new structure
- Fix import errors
- Update mocks/stubs as needed

**Manual smoke test:**
```bash
./scripts/quick_start.sh
uv run python scripts/test_proxy.py
# Verify UI at http://localhost:8000/v2/activity/monitor
```

**Validate observability (if enabled):**
```bash
./scripts/observability.sh up -d
# Check Grafana at http://localhost:3000
```

### 2.8 Final Cleanup

**Delete obsolete documentation:**
- Archive or delete v1-specific docs in `dev/archive/`
- Update `dev/context/` files to remove v1 references

**Update CHANGELOG.md:**
```markdown
## [2.0.0] - 2025-10-22

### Changed
- **BREAKING**: Completely replaced V1 architecture with V2 integrated architecture
- Single `proxy` service replaces separate `control-plane` + `litellm-proxy` services
- Queue-based policy interface (cleaner, more composable)
- Built-in OpenTelemetry observability
- Flattened `luthien_proxy.v2.*` namespace to `luthien_proxy.*`

### Added
- ToolCallJudge policy ported to V2 architecture

### Removed
- V1 control plane service
- V1 LiteLLM proxy integration
- V1 callback-based policy hooks
- Legacy configuration files
```

**Clear dev tracking files:**
```bash
echo "_No active objective._" > dev/OBJECTIVE.md
echo "" > dev/NOTES.md
```

## Acceptance Criteria

**Phase 1 Complete:**
- [ ] ToolCallJudge v2 implementation working
- [ ] All tests passing
- [ ] Validated with real LLM calls

**Phase 2 Complete:**
- [ ] No v1 code remaining in repository
- [ ] All imports updated (no `luthien_proxy.v2.*`)
- [ ] `docker compose up` starts single proxy service
- [ ] `./scripts/quick_start.sh` works end-to-end
- [ ] `./scripts/test_proxy.py` passes
- [ ] All tests passing (`./scripts/dev_checks.sh`)
- [ ] Documentation updated (README, CLAUDE.md, CHANGELOG)
- [ ] Manual smoke test successful

## Rollback Plan

If critical issues found:
1. `git revert` to commit before v1 deletion
2. Fix issues in v2
3. Re-attempt replacement

Git history preserves all v1 code if we need to reference it later.

## Timeline Estimate

- Phase 1: 3-4 hours
- Phase 2: 2-3 hours
- **Total: 5-7 hours**

## Next Steps

1. Start Phase 1: Port ToolCallJudge
2. After validation, proceed to Phase 2: Nuclear replacement
3. Update this plan with actual progress/learnings
