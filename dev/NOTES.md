# Notes

## 2025-10-28: Critical Type Safety Fix

**Bug:** StreamingResponseContext type mismatch
- Field declared as `ingress_assembler: StreamingChunkAssembler` (non-optional)
- But orchestrator instantiates with `ingress_assembler=None` before wiring assembler
- Pyright would flag: `None` incompatible with `StreamingChunkAssembler`

**Fix:** Made field optional with runtime guard
- Changed to `ingress_assembler: StreamingChunkAssembler | None`
- Added guard in `ingress_state` property: raises `RuntimeError` if accessed before initialization
- Assembler is set in `policy_processor` before any callbacks are invoked
- Type checking passes, runtime safety guaranteed

**Reference:** [pipeline-refactor-spec-v4.md:266](dev/pipeline-refactor-spec-v4.md#L266), [Type Safety section](dev/pipeline-refactor-spec-v4.md#L787)

---
