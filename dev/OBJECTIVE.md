# Objective: Improve AGENTS.md/CLAUDE.md Workflow

**Status**: Discussion/RFC with Jai

## Context
During the thinking blocks fix (#129 → PR #134), we discovered:
1. CLAUDE.md and AGENTS.md are near-identical copies (maintenance burden, drift risk)
2. Docs cover *workflow* well but lack *architectural data flow*
3. The 5-cycle debug journey happened because each layer was discovered only by hitting the next wall

## Proposed Changes

### 1. Consolidate CLAUDE.md and AGENTS.md
- Make AGENTS.md the canonical source (since Codex also uses this repo)
- CLAUDE.md becomes a thin wrapper that imports AGENTS.md + adds Claude-specific bits
- Reduces duplication and drift

### 2. Add Architectural Data Flow Doc
- Create `dev/context/data_flow.md`
- Document request flow through proxy layers
- Document known lossy conversions (format utils)
- Document validation layers

### 3. Workflow Improvement: Preserve NOTES in PRs
- Before clearing NOTES.md, add detailed content to PR description
- Extract reusable learnings to gotchas.md
- PR becomes permanent record of implementation journey

### 4. Proactive Gotchas Updates
- Add to gotchas.md during debugging, not just at the end
- "After hitting any unexpected wall, add to gotchas before fixing"

## Questions for Jai
1. AGENTS.md as source of truth - agree with this approach?
2. data_flow.md - useful? What format/level of detail?
3. Any concerns about the workflow changes?

## Acceptance Criteria
- [ ] Jai reviews and provides feedback
- [ ] Decide on consolidation approach
- [ ] Implement agreed changes
