# Objective: Improve AGENTS.md/CLAUDE.md Workflow

**Status**: Discussion/RFC with Jai

## Summary: Questions This PR Answers
| # | Question | Where |
|---|----------|-------|
| 1 | Why are CLAUDE.md and AGENTS.md duplicated? | [PR #135](https://github.com/LuthienResearch/luthien-proxy/pull/135) (this PR), Section 1 below |
| 2 | Why did the thinking blocks fix take 5 debug cycles? | [PR #134](https://github.com/LuthienResearch/luthien-proxy/pull/134) |
| 3 | Why do our demos keep breaking? | [PR #134](https://github.com/LuthienResearch/luthien-proxy/pull/134), Section 5 below |

## Context
During the thinking blocks fix (#129 → PR #134), we discovered:
1. CLAUDE.md and AGENTS.md are near-identical copies (maintenance burden, drift risk)
2. Docs cover *workflow* well but lack *architectural data flow*
3. The 5-cycle debug journey happened because each layer was discovered only by hitting the next wall

## Proposed Changes

### 1. Consolidate CLAUDE.md and AGENTS.md
- Make AGENTS.md the canonical source (since Codex also uses this repo)
- **Updated based on review feedback**: Claude Code reads CLAUDE.md as plain text, so "reference" approach won't work
- Options:
  - **Option A**: Symlink CLAUDE.md → AGENTS.md (add Claude-specific bits to AGENTS.md)
  - **Option B**: Build script that copies AGENTS.md → CLAUDE.md + appends Claude-specific content
  - **Option C**: Keep separate files but add CI check for drift
- Reduces duplication and drift

### 2. Add Architectural Data Flow Doc
- Create `dev/context/data_flow.md`
- Document request flow through proxy layers
- Document known lossy conversions (format utils)
- Document validation layers

### 3. Workflow Improvement: Preserve NOTES in PRs
- Before clearing NOTES.md, add detailed content to PR description
- Extract reusable learnings to gotchas.md
- **Updated based on review feedback**: PR descriptions can become stale and less discoverable
  - Prefer putting architectural insights into `dev/context/codebase_learnings.md`
  - Use PR description for debug logs and session-specific details
  - Committed files > PR descriptions for reusable knowledge

### 4. Proactive Gotchas Updates
- Add to gotchas.md during debugging, not just at the end
- "After hitting any unexpected wall, add to gotchas before fixing"

### 5. Add COE Process for Demo/Production Failures
**Context**: PR #134 (thinking blocks fix) required two COEs to understand one bug:
- COE #1: Why did the code fix take 5 debug cycles?
- COE #2: Why did the demo crash despite the fix being "merged"?

The fact that we needed a second COE means the first one missed something. That's a process smell.

**Proposal**: Add COE template that forces operational thinking:
- **Pre-merge checklist**: "What states can this code encounter in production?"
- **Demo readiness**: Treat demos as production deployments to humans
- **5 Whys required**: Root cause analysis before closing any incident

**Where to add**: Section in AGENTS.md (not a new file) with:
1. COE trigger criteria (when to write one)
2. Required sections (timeline, 5 Whys, action items)
3. Demo readiness checklist (manual test scenarios beyond unit tests)

## Questions for Jai
1. AGENTS.md as source of truth - agree with this approach?
2. data_flow.md - useful? What format/level of detail?
3. Any concerns about the workflow changes?
4. COE process: PR #134 needed two COEs for one bug. Is that a process smell worth fixing, or acceptable overhead?

## Acceptance Criteria
- [ ] Jai reviews and provides feedback
- [ ] Decide on consolidation approach (CLAUDE.md/AGENTS.md)
- [ ] Decide on COE process (add to AGENTS.md or defer)
- [ ] Implement agreed changes
